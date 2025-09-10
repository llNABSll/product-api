from __future__ import annotations
import json
import logging
import logging.config
import os
import re
import time
import uuid
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from typing import Any, Dict

from fastapi import Request, Response
from app.core.config import settings

ACCESS_LOGGER_NAME = "app.access"

# === Context ===
_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

def get_request_id() -> str | None:
    return _request_id_ctx.get()

def set_request_id(value: str | None) -> None:
    _request_id_ctx.set(value)

# === Filters ===
class ContextFilter(logging.Filter):
    """
    Injecte request_id et service dans chaque record.
    """
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def filter(self, record: logging.LogRecord) -> bool:
        rid = get_request_id()
        record.request_id = rid or "-"
        record.service = self.service_name
        return True

class SecretsFilter(logging.Filter):
    """
    Masque Authorization/JWT, mots de passe évidents, etc.
    IMPORTANT: on vide record.args si on modifie record.msg,
    sinon logging tentera de faire msg % args -> TypeError.
    """
    TOKEN_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._-]+")
    PWD_RE = re.compile(r'("password"\s*:\s*)"(.*?)"', re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        if not isinstance(record.msg, str):
            return False
        
        original_msg = record.msg
        msg = self.TOKEN_RE.sub(r"\1[REDACTED]", original_msg)
        msg = self.PWD_RE.sub(r'\1"[REDACTED]"', msg)
        
        if msg != original_msg:
            record.msg = msg
            record.args = ()
            return True
            
        return True

# === Formatters ===
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "service": getattr(record, "service", None),
            "request_id": getattr(record, "request_id", None),
        }
        # Ajoute ce qui existe (optionnel)
        for attr in ("method", "path", "status", "latency_ms", "client_ip", "user_agent"):
            val = getattr(record, attr, None)
            if val is not None:
                payload[attr] = val
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)

class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        rid = getattr(record, "request_id", "-")
        svc = getattr(record, "service", "-")
        return f"{base} [service={svc} rid={rid}]"

# === Handlers fabriqués en code (pour utiliser RotatingFileHandler proprement) ===
def _build_handler(filename: str, formatter: logging.Formatter) -> RotatingFileHandler:
    log_dir = settings.LOG_DIR
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, filename)
    h = RotatingFileHandler(
        path,
        maxBytes=settings.LOG_MAX_BYTES,
        backupCount=settings.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    h.setFormatter(formatter)
    h.addFilter(SecretsFilter())
    h.addFilter(ContextFilter(service_name=os.getenv("APP_NAME", "product-api")))
    return h

def setup_logging() -> None:
    """
    Idempotent: configure la racine + uvicorn loggers.
    """
    logger = logging.getLogger()
    if getattr(logger, "_configured", False):
        return

    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    is_json = settings.LOG_FORMAT.lower() == "json"

    app_formatter = JsonFormatter() if is_json else PlainFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    access_formatter = JsonFormatter() if is_json else PlainFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Handlers
    app_file = _build_handler(settings.LOG_FILE, app_formatter)
    access_file = _build_handler(settings.LOG_ACCESS_FILE, access_formatter)

    # Console handler optionnel
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(app_formatter)
    console.addFilter(SecretsFilter())
    console.addFilter(ContextFilter(service_name=os.getenv("APP_NAME", "product-api")))

    # Root logger
    logger.setLevel(level)
    logger.handlers.clear()
    logger.addHandler(app_file)
    if settings.LOG_ENABLE_CONSOLE:
        logger.addHandler(console)

    # Access logger dédié
    access_logger = logging.getLogger(ACCESS_LOGGER_NAME)
    access_logger.setLevel(level)
    access_logger.handlers.clear()
    access_logger.addHandler(access_file)
    if settings.LOG_ENABLE_CONSOLE:
        access_logger.addHandler(console)
    access_logger.propagate = False

    # Harmoniser uvicorn
    for name in ("uvicorn", "uvicorn.error"):
        l = logging.getLogger(name)
        l.setLevel(level)
        l.handlers.clear()
        l.addHandler(app_file)
        if settings.LOG_ENABLE_CONSOLE:
            l.addHandler(console)
        l.propagate = False

    # Garder l’access d’Uvicorn silencieux (on loggue nous-mêmes)
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.access").propagate = False

    logger._configured = True  # type: ignore[attr-defined]

# === Middleware Access Log ===
async def access_log_middleware(request: Request, call_next):
    """
    - Génère/propague un X-Request-ID (header)
    - Logge 1 ligne access (method, path, status, durée, ip, UA)
    - Injecte request_id dans le contexte logging
    """
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    set_request_id(rid)

    start = time.perf_counter()
    try:
        response: Response = await call_next(request)
    except Exception:
        # On log l'exception côté access avec les métadonnées...
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logging.getLogger(ACCESS_LOGGER_NAME).exception(
            "unhandled exception",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": 500,
                "latency_ms": duration_ms,
                "client_ip": request.client.host if request.client else None,
                "user_agent": request.headers.get("user-agent"),
                "request_id": rid,
            },
        )
        set_request_id(None)
        raise
    else:
        # Succès: on log l'accès et on renvoie le header
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logging.getLogger(ACCESS_LOGGER_NAME).info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": duration_ms,
                "client_ip": request.client.host if request.client else None,
                "user_agent": request.headers.get("user-agent"),
                "request_id": rid,
            },
        )
        response.headers["X-Request-ID"] = rid
        set_request_id(None)
        return response

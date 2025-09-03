from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import time
import os

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from app.core.config import settings
from app.core.database import Base, engine
from app.core.rabbitmq import rabbitmq
from app.core.logging import setup_logging, access_log_middleware
from app.routers.product_router import router
from sqlalchemy import text

from app.models import Product

# --- Logging ---
setup_logging()
logger = logging.getLogger(__name__)

# --- Prometheus ---
REQUEST_COUNT = Counter("http_requests_total", "Total des requêtes HTTP", ["method", "path", "status"])
REQUEST_LATENCY = Histogram("http_request_duration_seconds", "Latence des requêtes HTTP", ["method", "path"])

# --- Lifespan (startup/shutdown) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # DB connectivity check
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("database connection OK")
    except Exception:
        logger.exception("database connectivity check failed")

    # Création du schéma (remplace Alembic pour ce projet)
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("database schema ensured (create_all)")
    except Exception:
        logger.exception("database schema create_all failed")

    # RabbitMQ
    try:
        await rabbitmq.connect()
        logger.info("rabbitmq connected")
    except Exception:
        logger.exception("rabbitmq connect failed; continuing without it")

    yield

    # Shutdown MQ
    try:
        await rabbitmq.disconnect()
        logger.info("rabbitmq disconnected")
    except Exception:
        logger.exception("rabbitmq disconnect failed")

# --- Application FastAPI ---
app = FastAPI(
    title=settings.APP_TITLE,
    description=settings.APP_DESCRIPTION,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    root_path=os.getenv("ROOT_PATH", ""),  # vide si tu relies via Traefik StripPrefix
    docs_url="/docs" if settings.ENV != "prod" else None,
    redoc_url="/redoc" if settings.ENV != "prod" else None,
    openapi_url="/openapi.json" if settings.ENV != "prod" else None,
)

# --- Middlewares HTTP ---
app.middleware("http")(access_log_middleware)

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.time()
    response: Response = await call_next(request)
    duration = time.time() - start

    path = request.url.path
    if path.startswith("/products/") and len(path.split("/")) == 3:
        path = "/products/{id}"

    REQUEST_COUNT.labels(request.method, path, str(response.status_code)).inc()
    REQUEST_LATENCY.labels(request.method, path).observe(duration)
    return response

# --- CORS ---
allow_methods = (
    ["*"] if settings.CORS_ALLOW_METHODS == "*"
    else [m.strip() for m in settings.CORS_ALLOW_METHODS.split(",") if m.strip()]
)
allow_headers = (
    ["*"] if settings.CORS_ALLOW_HEADERS == "*"
    else [h.strip() for h in settings.CORS_ALLOW_HEADERS.split(",") if h.strip()]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOW_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=allow_methods,
    allow_headers=allow_headers,
)

# --- Endpoints techniques ---
@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}

# --- Routers ---
app.include_router(router)

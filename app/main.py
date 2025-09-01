from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import time

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from app.core.config import settings
from app.core.database import Base, engine
from app.core.rabbitmq import rabbitmq
from app.core.logging import setup_logging, access_log_middleware
from app.routers import product_router
from sqlalchemy import text


# --- Logging ---
# Configure le logging (JSON/texte, rotation, masquage de secrets, request-id)
# AVANT l'instanciation de l'app, pour capter les logs de démarrage.
setup_logging()
logger = logging.getLogger(__name__)

# --- Prometheus (métriques globales) ---
# Attention à la cardinalité des labels: on normalise certains chemins dans le middleware.
REQUEST_COUNT = Counter("http_requests_total", "Total des requêtes HTTP", ["method", "path", "status"])
REQUEST_LATENCY = Histogram("http_request_duration_seconds", "Latence des requêtes HTTP", ["method", "path"])

# --- Lifespan (startup/shutdown) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("database connection OK")
    except Exception:
        logger.exception("database connectivity check failed")

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
)

# --- Middlewares HTTP ---
# Access log + propagation du X-Request-ID (doit être enregistré tôt pour couvrir toute la chaîne).
app.middleware("http")(access_log_middleware)

# Collecte métriques Prometheus; normalise certains chemins pour limiter la cardinalité.
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.time()
    response: Response = await call_next(request)
    duration = time.time() - start

    path = request.url.path
    # Exemple de normalisation (évite une explosion de séries /products/123, /products/456, etc.)
    if path.startswith("/products/") and len(path.split("/")) == 3:
        path = "/products/{id}"

    REQUEST_COUNT.labels(request.method, path, str(response.status_code)).inc()
    REQUEST_LATENCY.labels(request.method, path).observe(duration)
    return response

# --- CORS ---
# Paramétrage piloté par la config (origines, méthodes, en-têtes).
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
    # Expose les métriques Prometheus au format texte standard.
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/health", tags=["health"])
def health():
    # Sonde de liveness simple. Pour une readiness, ajouter des checks DB/MQ si nécessaire.
    return {"status": "ok"}

# --- Routers ---
# Les routeurs doivent rester fins (I/O, validation). La logique métier vit dans services/.
app.include_router(product_router.router, prefix="/products", tags=["produits"])

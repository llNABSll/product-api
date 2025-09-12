from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from sqlalchemy import text

from app.core.config import settings
from app.core.database import Base, engine
from app.core.logging import setup_logging, access_log_middleware
from app.infra.events.rabbitmq import rabbitmq, start_consumer
from app.infra.events.handlers import handle_order_created, handle_order_deleted, handle_order_updated
from app.api.routes import product
from app.core.database import SessionLocal

# --- Logging ---
setup_logging()
logger = logging.getLogger(__name__)

# --- Prometheus ---
REQUEST_COUNT = Counter(
    "http_requests_total", "Total des requêtes HTTP", ["method", "path", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "Latence des requêtes HTTP", ["method", "path"]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("database connection OK")
    except Exception:
        logger.exception("database connectivity check failed")

    try:
        Base.metadata.create_all(bind=engine)
        logger.info("database schema ensured (create_all)")
    except Exception:
        logger.exception("database schema create_all failed")

    try:
        await rabbitmq.connect()

        async def consumer_handler(payload: dict, rk: str):
            logger.info("[product-api] received %s: %s", rk, payload)
            db = SessionLocal()
            try:
                if rk == "order.created":
                    await handle_order_created(payload, db)
                elif rk == "order.deleted":
                    await handle_order_deleted(payload, db)
                elif rk == "order.updated":
                    await handle_order_updated(payload, db)
                elif rk == "order.rejected":
                    await handle_order_deleted(payload, db) 
                else:
                    logger.warning(f"[product-api] event ignoré: {rk}")
            finally:
                db.close()

        asyncio.create_task(
            start_consumer(
                rabbitmq.connection,
                rabbitmq.exchange,
                rabbitmq.exchange_type,
                queue_name="product-events",
                patterns=["order.#", "customer.#"],
                handler=consumer_handler,
            )
        )

        logger.info("RabbitMQ consumer started")
    except Exception:
        logger.exception("RabbitMQ startup failed")

    yield  

    # --- Shutdown ---
    try:
        await rabbitmq.disconnect()
        logger.info("RabbitMQ disconnected")
    except Exception:
        pass


app = FastAPI(
    title=settings.APP_TITLE,
    description=settings.APP_DESCRIPTION,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    root_path=os.getenv("ROOT_PATH", ""),
    docs_url="/docs" if settings.ENV != "prod" else None,
    redoc_url="/redoc" if settings.ENV != "prod" else None,
    openapi_url="/openapi.json" if settings.ENV != "prod" else None,
)

# --- Middlewares ---
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
    ["*"]
    if settings.CORS_ALLOW_METHODS == "*"
    else [m.strip() for m in settings.CORS_ALLOW_METHODS.split(",") if m.strip()]
)
allow_headers = (
    ["*"]
    if settings.CORS_ALLOW_HEADERS == "*"
    else [h.strip() for h in settings.CORS_ALLOW_HEADERS.split(",") if h.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOW_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=allow_methods,
    allow_headers=allow_headers,
)

# --- Tech endpoints ---
@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}


# --- Routes ---
app.include_router(product.router)

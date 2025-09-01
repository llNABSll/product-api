# app/core/rabbitmq.py
from __future__ import annotations
import asyncio, json, logging
from typing import Optional, Awaitable, Callable

import aio_pika
from aio_pika.abc import AbstractRobustConnection, AbstractRobustChannel, AbstractExchange
import anyio
from anyio import from_thread

from app.core.config import settings

logger = logging.getLogger(__name__)

_EX_TYPE_MAP = {
    "fanout": aio_pika.ExchangeType.FANOUT,
    "direct": aio_pika.ExchangeType.DIRECT,
    "topic":  aio_pika.ExchangeType.TOPIC,
    "headers": aio_pika.ExchangeType.HEADERS,
}

class RabbitMQ:
    def __init__(self) -> None:
        self.connection: Optional[AbstractRobustConnection] = None
        self.channel: Optional[AbstractRobustChannel] = None
        self.exchange: Optional[AbstractExchange] = None

    async def connect(self) -> None:
        """Ouvre une connexion + channel et déclare l'exchange durable."""
        if not settings.RABBITMQ_URL:
            logger.info("RabbitMQ désactivé (URL non définie)")
            return
        try:
            self.connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
            self.channel = await self.connection.channel(publisher_confirms=True)
            await self.channel.set_qos(10)

            ex_type = _EX_TYPE_MAP.get(
                (settings.RABBITMQ_EXCHANGE_TYPE or "fanout").lower(),
                aio_pika.ExchangeType.FANOUT,
            )
            self.exchange = await self.channel.declare_exchange(
                settings.RABBITMQ_EXCHANGE or "products",
                ex_type,
                durable=True,
            )
            logger.info(
                "RabbitMQ prêt (exchange=%s, type=%s)",
                settings.RABBITMQ_EXCHANGE or "products",
                settings.RABBITMQ_EXCHANGE_TYPE or "fanout",
            )
        except Exception:
            logger.exception("Échec connexion/initialisation RabbitMQ")
            self.channel = None
            self.exchange = None

    async def disconnect(self) -> None:
        try:
            if self.channel and not self.channel.is_closed:
                await self.channel.close()
        except Exception:
            logger.exception("Échec fermeture channel RabbitMQ")
        try:
            if self.connection and not self.connection.is_closed:
                await self.connection.close()
        except Exception:
            logger.exception("Échec fermeture connexion RabbitMQ")
        finally:
            self.exchange = None
            self.channel = None
            self.connection = None

    def _ready(self) -> bool:
        return bool(self.exchange) and bool(self.channel) and not self.channel.is_closed

    async def _ensure_ready(self) -> bool:
        """Reconnecte / redéclare l'exchange si nécessaire."""
        if self._ready():
            return True
        logger.warning("RabbitMQ non prêt; tentative de reconnexion…")
        try:
            await self.connect()
            return self._ready()
        except Exception:
            logger.exception("Reconnexion RabbitMQ échouée")
            return False

    # -------- Publication (payload str / json) --------

    async def publish(self, payload: str) -> None:
        """Publie sur l'exchange configuré (fanout: routing_key ignorée)."""
        if not settings.RABBITMQ_URL:
            return
        if not await self._ensure_ready():
            logger.warning("RabbitMQ channel indisponible; publish ignoré")
            return
        assert self.exchange is not None
        try:
            msg = aio_pika.Message(
                body=payload.encode("utf-8"),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            )
            await self.exchange.publish(msg, routing_key="")
            logger.info("RabbitMQ: message publié (%d bytes)", len(payload))
        except Exception:
            logger.exception("RabbitMQ publish échoué")

    async def publish_json(self, data: dict) -> None:
        await self.publish(json.dumps(data))

    # -------- Envoi direct vers une queue (optionnel) --------

    async def send(self, queue_name: str, payload: str, *, durable: bool = True) -> None:
        if not settings.RABBITMQ_URL:
            return
        if not await self._ensure_ready():
            logger.warning("RabbitMQ channel indisponible; envoi ignoré")
            return
        try:
            assert self.channel is not None
            await self.channel.declare_queue(queue_name, durable=durable)
            await self.channel.default_exchange.publish(
                aio_pika.Message(body=payload.encode("utf-8")),
                routing_key=queue_name,
            )
            logger.info("RabbitMQ: send -> %s", queue_name)
        except Exception:
            logger.exception("RabbitMQ send échoué")

    async def send_json(self, queue_name: str, data: dict) -> None:
        await self.send(queue_name, json.dumps(data))

rabbitmq = RabbitMQ()

# -------- Helpers pour l'app --------

async def publish_event_async(event: str, payload: dict) -> None:
    """À utiliser depuis un handler async FastAPI."""
    if not settings.RABBITMQ_URL:
        return
    await rabbitmq.publish_json({"event": event, **payload})

def publish_event(event: str, payload: dict) -> None:
    """
    À utiliser depuis du code *sync* (ex: dépendances/handlers sync).
    Planifie la publish sur la loop ASGI si on est dans un thread,
    sinon lance une loop locale (scripts).
    """
    if not settings.RABBITMQ_URL:
        return
    try:
        # On est probablement dans un thread (FastAPI sync) -> exécuter sur la loop
        from_thread.run(publish_event_async, event, payload)
    except RuntimeError:
        # Pas de loop active -> loop locale
        asyncio.run(publish_event_async(event, payload))

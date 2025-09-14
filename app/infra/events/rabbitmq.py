from __future__ import annotations

import json
import logging
from typing import Iterable, Awaitable, Callable

import aio_pika

from app.core.config import settings

logger = logging.getLogger(__name__)

_EXCHANGE_TYPE_MAP = {
    "topic": aio_pika.ExchangeType.TOPIC,
    "fanout": aio_pika.ExchangeType.FANOUT,
    "direct": aio_pika.ExchangeType.DIRECT,
    "headers": aio_pika.ExchangeType.HEADERS,
}


class RabbitMQ:
    def __init__(self):
        # URL commune (fallback par défaut)
        self.url = settings.RABBITMQ_URL or "amqp://app:app@rabbitmq:5672/%2F"

        # Exchange + type (pilotés par l'env)
        self.exchange_name = settings.RABBITMQ_EXCHANGE or "events"
        self.exchange_type = _EXCHANGE_TYPE_MAP.get(
            (settings.RABBITMQ_EXCHANGE_TYPE or "topic").lower(),
            aio_pika.ExchangeType.TOPIC,
        )

        self.connection: aio_pika.RobustConnection | None = None
        self.channel: aio_pika.Channel | None = None
        self.exchange: aio_pika.Exchange | None = None

    async def connect(self):
        """Connexion robuste + déclaration de l'exchange."""
        self.connection = await aio_pika.connect_robust(self.url)
        self.channel = await self.connection.channel()
        self.exchange = await self.channel.declare_exchange(
            self.exchange_name, self.exchange_type, durable=True
        )
        logger.info(
            "RabbitMQ connected. Exchange '%s' (%s) declared.",
            self.exchange_name,
            self.exchange_type.name.lower(),
        )

    async def disconnect(self):
        try:
            if self.channel and not self.channel.is_closed:
                await self.channel.close()
                logger.info("RabbitMQ channel closed.")
        except Exception:
            logger.exception("Failed to close RabbitMQ channel.")

        try:
            if self.connection and not self.connection.is_closed:
                await self.connection.close()
                logger.info("RabbitMQ connection closed.")
        except Exception:
            logger.exception("Failed to close RabbitMQ connection.")

    async def publish_message(self, routing_key: str, message: dict):
        """Publie un message. (routing_key ignorée si fanout)"""
        if not self.exchange:
            logger.error("Cannot publish: exchange is not available (connect() not called).")
            return

        try:
            rk = routing_key if self.exchange_type == aio_pika.ExchangeType.TOPIC else ""
            await self.exchange.publish(
                aio_pika.Message(
                    body=json.dumps(message).encode("utf-8"),
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key=rk,
            )
            logger.info("Published rk=%s, payload=%s", routing_key, message)
        except Exception:
            logger.exception("Failed to publish rk=%s", routing_key)


rabbitmq = RabbitMQ()


# ---------- Consommation (topic ou fanout) ----------
async def start_consumer(
    connection: aio_pika.RobustConnection,
    exchange: aio_pika.Exchange,
    exchange_type: aio_pika.ExchangeType,
    queue_name: str,
    patterns: Iterable[str],
    handler: Callable[[dict, str], Awaitable[None]],
):
    """
    - topic: bind sur chaque pattern fourni (ex: 'order.#', 'customer.#')
    - fanout: ignore les patterns et bind sans routing_key
    """
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=16)

    queue = await channel.declare_queue(queue_name, durable=True, auto_delete=False)

    if exchange_type == aio_pika.ExchangeType.FANOUT:
        await queue.bind(exchange, routing_key="")
        logger.info("Queue %s bound (fanout)", queue_name)
    else:
        for p in patterns:
            await queue.bind(exchange, routing_key=p)
            logger.info("Queue %s bound to pattern %s", queue_name, p)

    async with queue.iterator() as it:
        async for message in it:
            async with message.process():
                rk = message.routing_key
                try:
                    payload = json.loads(message.body.decode("utf-8"))
                except Exception:
                    payload = {"raw": message.body}
                try:
                    await handler(payload, rk)
                except Exception:
                    logger.exception("Handler error rk=%s", rk)

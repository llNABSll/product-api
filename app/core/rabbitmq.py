# app/core/rabbitmq.py
from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional

import aio_pika
from anyio import from_thread

from app.core.config import settings

logger = logging.getLogger(__name__)


class RabbitMQ:
    """
    Client RabbitMQ basé sur aio-pika.
    - Connexion robuste (reconnect interne d'aio-pika).
    - Publisher confirms activés pour garantir la livraison côté broker.
    - API simple send/publish + helpers JSON.
    """

    def __init__(self) -> None:
        self.connection: Optional[aio_pika.RobustConnection] = None
        self.channel: Optional[aio_pika.RobustChannel] = None

    # --- Connexion / fermeture ---

    async def connect(self) -> None:
        """
        Établit la connexion et ouvre un channel.
        Si RABBITMQ_URL est absent, on considère RabbitMQ désactivé (no-op).
        """
        if not settings.RABBITMQ_URL:
            logger.info("RabbitMQ désactivé (RABBITMQ_URL non défini)")
            return
        try:
            self.connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
            # publisher_confirms=True : on attend les acks broker pour chaque publish
            self.channel = await self.connection.channel(publisher_confirms=True)
            logger.info("RabbitMQ connecté")
        except Exception:
            logger.exception("Échec connexion RabbitMQ")
            # Laisse self.channel = None ; les calls suivants deviendront no-op logués.

    async def disconnect(self) -> None:
        """
        Ferme proprement le channel puis la connexion (idempotent).
        """
        try:
            if self.channel and not self.channel.is_closed:
                await self.channel.close()
                logger.info("RabbitMQ channel fermé")
        except Exception:
            logger.exception("Échec fermeture channel RabbitMQ")

        try:
            if self.connection and not self.connection.is_closed:
                await self.connection.close()
                logger.info("RabbitMQ connexion fermée")
        except Exception:
            logger.exception("Échec fermeture connexion RabbitMQ")

    # --- Consommation ---

    async def consume(
        self,
        queue_name: str,
        callback: Callable[[bytes], Optional[Awaitable[None]]],
        *,
        prefetch: int = 10,
        durable: bool = True,
        requeue_on_error: bool = False,
    ) -> None:
        """
        Consomme les messages d'une queue et appelle `callback(body: bytes)`.
        - `callback` peut être sync ou async.
        - `requeue_on_error=False` par défaut pour éviter les boucles poison-message.
        """
        if not self.channel:
            logger.warning("RabbitMQ channel indisponible; consommation ignorée")
            return

        await self.channel.set_qos(prefetch_count=prefetch)
        queue = await self.channel.declare_queue(queue_name, durable=durable)
        logger.info("Consommation démarrée", extra={"queue": queue_name, "prefetch": prefetch})

        async with queue.iterator() as it:
            async for message in it:
                # message.process gère ack/nack automatiquement selon les exceptions
                async with message.process(requeue=requeue_on_error):
                    try:
                        result = callback(message.body)
                        if asyncio.iscoroutine(result):
                            await result  # support des callbacks async
                    except Exception:
                        logger.exception("Erreur callback consommateur", extra={"queue": queue_name})
                        # lever une exception ici déclenchera nack (requeue selon paramètre)
                        raise

    # --- Publication ---

    async def send(self, queue_name: str, payload: str, *, durable: bool = True) -> None:
        """
        Envoie un message vers une queue (routing_key = queue_name).
        """
        if not self.channel:
            logger.warning("RabbitMQ channel indisponible; envoi ignoré", extra={"queue": queue_name})
            return
        try:
            await self.channel.declare_queue(queue_name, durable=durable)
            await self.channel.default_exchange.publish(
                aio_pika.Message(body=payload.encode("utf-8")),
                routing_key=queue_name,
            )
            logger.info("Message envoyé", extra={"queue": queue_name, "size": len(payload)})
        except Exception:
            logger.exception("Échec envoi message", extra={"queue": queue_name})

    async def publish(self, exchange_name: str, payload: str) -> None:
        """
        Publie un message sur un exchange (fanout/topic selon config).
        """
        if not self.channel:
            logger.warning("RabbitMQ channel indisponible; publish ignoré", extra={"exchange": exchange_name})
            return
        try:
            # Type d'exchange configurable (fanout par défaut).
            exchange_type = getattr(aio_pika.ExchangeType, settings.RABBITMQ_EXCHANGE_TYPE.lower(), aio_pika.ExchangeType.FANOUT)
            exchange = await self.channel.declare_exchange(exchange_name, exchange_type, durable=True)
            await exchange.publish(aio_pika.Message(body=payload.encode("utf-8")), routing_key="")
            logger.info("Message publié", extra={"exchange": exchange_name, "size": len(payload)})
        except Exception:
            logger.exception("Échec publication message", extra={"exchange": exchange_name})

    # --- Helpers JSON ---

    async def send_json(self, queue_name: str, data: dict) -> None:
        await self.send(queue_name, json.dumps(data))

    async def publish_json(self, exchange_name: str, data: dict) -> None:
        await self.publish(exchange_name, json.dumps(data))


# Instance globale (facile à monkeypatcher en tests)
rabbitmq = RabbitMQ()


def publish_event(event: str, payload: dict) -> None:
    """
    Helper synchrone pour publier un événement JSON sur l'exchange configuré.
    - Utilise anyio.from_thread.run lorsqu'on est dans un thread (cas FastAPI sync).
    - Sinon, lance une boucle asyncio locale (scripts, tests).
    - No-op si RabbitMQ est désactivé (RABBITMQ_URL absent).
    """
    if not settings.RABBITMQ_URL:
        return  # no-op en dev/test sans broker

    data = {"event": event, **payload}
    exchange = settings.RABBITMQ_EXCHANGE or "products"

    try:
        # Contexte thread (FastAPI sync def) -> exécuter la coroutine sur la loop ASGI
        from_thread.run(rabbitmq.publish_json, exchange, data)
    except RuntimeError:
        # Pas de loop en cours -> exécuter une loop locale
        asyncio.run(rabbitmq.publish_json(exchange, data))

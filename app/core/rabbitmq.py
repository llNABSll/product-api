import aio_pika
from app.core.config import settings

import logging

logger = logging.getLogger('RABBITMQ')
logging.basicConfig(level=logging.DEBUG)

class RabbitMQ:
    def __init__(self):
        self.connection = None
        self.channel = None

    async def connect(self):
        try:
            self.connection = await aio_pika.connect_robust(settings.RABBITMQ_URL)
            self.channel = await self.connection.channel()
            logger.info("RabbitMQ connected")
        except Exception as e:
            logger.error(f"Failed to connect to RabbitMQ: {e}")

    async def disconnect(self):
        if self.channel and not self.channel.is_closed:
            await self.channel.close()
            logger.info("RabbitMQ channel closed")
        if self.connection and not self.connection.is_closed:
            await self.connection.close()
            logger.info("RabbitMQ connection closed")


    async def consume(self, queue_name, callback):
        if not self.channel:
            logger.error("RabbitMQ channel not available")
            return

        await self.channel.set_qos(prefetch_count=10)
        queue = await self.channel.declare_queue(queue_name, durable=True)

        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process():
                    callback(message.body)

    async def send(self, queue_name, payload):
        if not self.channel:
            logger.error("RabbitMQ channel not available")
            return

        try:
            await self.channel.declare_queue(queue_name, durable=True)
            await self.channel.default_exchange.publish(
                aio_pika.Message(body=payload.encode()),
                routing_key=queue_name,
            )
            logger.info(f"Message sent to {queue_name}")
        except Exception as e:
            logger.error(f"Failed to send message to {queue_name}: {e}")

    async def publish(self, exchange_name, payload):
        if not self.channel:
            logger.error("RabbitMQ channel not available")
            return

        try:
            exchange = await self.channel.declare_exchange(exchange_name, aio_pika.ExchangeType.FANOUT)
            await exchange.publish(
                aio_pika.Message(body=payload.encode()),
                routing_key="",
            )
            logger.info(f"Message published to {exchange_name}")
        except Exception as e:
            logger.error(f"Failed to publish message to {exchange_name}: {e}")

    async def subscribe(self, exchange_name, callback):
        if not self.channel:
            logger.error("RabbitMQ channel not available")
            return

        exchange = await self.channel.declare_exchange(exchange_name, aio_pika.ExchangeType.FANOUT)
        queue = await self.channel.declare_queue(exclusive=True)
        await queue.bind(exchange)

        await queue.consume(callback)



rabbitmq = RabbitMQ()

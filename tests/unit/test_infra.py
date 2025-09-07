# tests/test_infra.py
import pytest
import json
import logging
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Configure logging for this test module
@pytest.fixture(autouse=True, scope="module")
def configure_logging():
    logging.basicConfig(level=logging.DEBUG)
    yield

import aio_pika

from app.infra.events import contracts
from app.infra.events.rabbitmq import RabbitMQ, start_consumer


# ---------- contracts.py ----------

def test_message_publisher_protocol():
    class DummyPublisher:
        async def publish_message(self, routing_key: str, message: dict) -> None:
            return f"{routing_key}:{message}"

    pub: contracts.MessagePublisher = DummyPublisher()
    result = asyncio.run(pub.publish_message("rk", {"a": 1}))
    assert "rk" in result


def test_message_consumer_protocol():
    class DummyConsumer:
        async def start_consumer(self, connection, exchange, exchange_type, *args, **kwargs):
            await kwargs["handler"]({"msg": "ok"}, "rk")

    cons: contracts.MessageConsumer = DummyConsumer()

    async def handler(payload, rk):
        assert payload["msg"] == "ok"
        assert rk == "rk"

    asyncio.run(cons.start_consumer(None, None, None, queue_name="q", patterns=["rk"], handler=handler))


# ---------- rabbitmq.py ----------

@pytest.mark.asyncio
async def test_connect_and_disconnect():
    fake_connection = AsyncMock()
    fake_channel = AsyncMock()
    fake_exchange = AsyncMock()

    fake_connection.channel.return_value = fake_channel
    fake_channel.declare_exchange.return_value = fake_exchange

    with patch("aio_pika.connect_robust", return_value=fake_connection):
        mq = RabbitMQ()
        await mq.connect()
        assert mq.connection is fake_connection
        assert mq.channel is fake_channel
        assert mq.exchange is fake_exchange

        # simuler ouverts
        mq.channel.is_closed = False
        mq.connection.is_closed = False

        await mq.disconnect()
        fake_channel.close.assert_awaited()
        fake_connection.close.assert_awaited()


@pytest.mark.asyncio
async def test_disconnect_handles_exceptions(caplog):
    mq = RabbitMQ()
    mq.channel = AsyncMock()
    mq.connection = AsyncMock()
    mq.channel.is_closed = False
    mq.connection.is_closed = False
    mq.channel.close.side_effect = Exception("channel error")
    mq.connection.close.side_effect = Exception("conn error")

    caplog.set_level(logging.ERROR)
    await mq.disconnect()
    assert "Failed to close" in caplog.text


@pytest.mark.asyncio
async def test_publish_message_success():
    # Le mock doit spécifier que sa méthode `publish` est aussi un mock asynchrone
    fake_exchange = AsyncMock(publish=AsyncMock())
    mq = RabbitMQ()
    mq.exchange = fake_exchange
    mq.exchange_type = aio_pika.ExchangeType.TOPIC

    await mq.publish_message("rk", {"hello": "world"})
    fake_exchange.publish.assert_awaited()
    args, kwargs = fake_exchange.publish.call_args
    assert isinstance(args[0], aio_pika.Message)
    assert kwargs["routing_key"] == "rk"


@pytest.mark.asyncio
async def test_publish_message_no_exchange(caplog):
    mq = RabbitMQ()
    mq.exchange = None
    # On doit cibler le logger spécifique utilisé par le module rabbitmq
    with caplog.at_level(logging.ERROR, logger="app.infra.events.rabbitmq"):
        await mq.publish_message("rk", {"x": 1})
    assert "Cannot publish" in caplog.text


@pytest.mark.asyncio
async def test_publish_message_exception(caplog):
    # Le mock doit spécifier que sa méthode `publish` est aussi un mock asynchrone
    fake_exchange = AsyncMock(publish=AsyncMock(side_effect=Exception("boom")))
    mq = RabbitMQ()
    mq.exchange = fake_exchange
    mq.exchange_type = aio_pika.ExchangeType.TOPIC
    # On doit cibler le logger spécifique utilisé par le module rabbitmq
    with caplog.at_level(logging.ERROR, logger="app.infra.events.rabbitmq"):
        await mq.publish_message("rk", {"fail": True})
    assert "Failed to publish" in caplog.text


# ---------- helpers pour consumer ----------

class FakeAsyncIterator:
    """Simule queue.iterator() comme un vrai async context manager"""
    def __init__(self, messages=None):
        self._messages = messages or []

    async def __aenter__(self):
        async def gen():
            for m in self._messages:
                yield m
        return gen()

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_start_consumer_topic():
    fake_queue = AsyncMock()
    fake_channel = AsyncMock()
    fake_channel.declare_queue.return_value = fake_queue
    fake_connection = AsyncMock()
    fake_connection.channel.return_value = fake_channel
    fake_exchange = AsyncMock()

    fake_message = MagicMock()
    fake_message.routing_key = "rk"
    fake_message.body = json.dumps({"foo": "bar"}).encode()
    fake_message.process.return_value.__aenter__ = AsyncMock()
    fake_message.process.return_value.__aexit__ = AsyncMock()

    # 👉 injecte fake iterator avec 1 message
    fake_queue.iterator.return_value = FakeAsyncIterator([fake_message])

    async def handler(payload, rk):
        assert payload["foo"] == "bar"
        assert rk == "rk"

    task = asyncio.create_task(
        start_consumer(fake_connection, fake_exchange, aio_pika.ExchangeType.TOPIC,
                       queue_name="q", patterns=["rk"], handler=handler)
    )
    await asyncio.sleep(0.05)
    task.cancel()

    fake_queue.bind.assert_awaited_with(fake_exchange, routing_key="rk")


@pytest.mark.asyncio
async def test_start_consumer_fanout():
    fake_queue = AsyncMock()
    fake_channel = AsyncMock()
    fake_channel.declare_queue.return_value = fake_queue
    fake_connection = AsyncMock()
    fake_connection.channel.return_value = fake_channel
    fake_exchange = AsyncMock()

    async def handler(payload, rk): ...

    # 👉 pas de messages
    fake_queue.iterator.return_value = FakeAsyncIterator([])

    task = asyncio.create_task(
        start_consumer(fake_connection, fake_exchange, aio_pika.ExchangeType.FANOUT,
                       queue_name="q", patterns=[], handler=handler)
    )
    await asyncio.sleep(0.05)
    task.cancel()

    fake_queue.bind.assert_awaited_with(fake_exchange, routing_key="")

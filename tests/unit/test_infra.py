import asyncio
import json
import logging
from unittest.mock import AsyncMock, patch

import pytest
import aio_pika

from app.infra.events import contracts
from app.infra.events.rabbitmq import RabbitMQ, start_consumer, _EXCHANGE_TYPE_MAP


# ------------------------------
# Fixtures globales
# ------------------------------

@pytest.fixture(autouse=True, scope="module")
def configure_logging():
    logging.basicConfig(level=logging.DEBUG)
    yield


# ------------------------------
# Protocols
# ------------------------------

def test_message_publisher_protocol():
    class DummyPublisher:
        async def publish_message(self, routing_key: str, message: dict) -> str:
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
        assert payload == {"msg": "ok"}
        assert rk == "rk"

    asyncio.run(cons.start_consumer(None, None, None, queue_name="q", patterns=["rk"], handler=handler))


# ------------------------------
# RabbitMQ connect/disconnect
# ------------------------------

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
    assert "Failed to close RabbitMQ channel" in caplog.text
    assert "Failed to close RabbitMQ connection" in caplog.text


@pytest.mark.asyncio
async def test_disconnect_already_closed():
    mq = RabbitMQ()
    mq.channel = AsyncMock()
    mq.connection = AsyncMock()
    mq.channel.is_closed = True
    mq.connection.is_closed = True

    # Ne doit pas planter ni appeler close()
    await mq.disconnect()
    mq.channel.close.assert_not_called()
    mq.connection.close.assert_not_called()


# ------------------------------
# publish_message
# ------------------------------

@pytest.mark.asyncio
async def test_publish_message_success():
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
    with caplog.at_level(logging.ERROR, logger="app.infra.events.rabbitmq"):
        await mq.publish_message("rk", {"x": 1})
    assert "Cannot publish" in caplog.text


@pytest.mark.asyncio
async def test_publish_message_exception(caplog):
    fake_exchange = AsyncMock(publish=AsyncMock(side_effect=Exception("boom")))
    mq = RabbitMQ()
    mq.exchange = fake_exchange
    mq.exchange_type = aio_pika.ExchangeType.TOPIC
    with caplog.at_level(logging.ERROR, logger="app.infra.events.rabbitmq"):
        await mq.publish_message("rk", {"fail": True})
    assert "Failed to publish" in caplog.text


@pytest.mark.asyncio
async def test_publish_message_fanout():
    fake_exchange = AsyncMock(publish=AsyncMock())
    mq = RabbitMQ()
    mq.exchange = fake_exchange
    mq.exchange_type = aio_pika.ExchangeType.FANOUT

    await mq.publish_message("ignored_key", {"test": True})
    args, kwargs = fake_exchange.publish.call_args
    # routing_key doit être forcé à ""
    assert kwargs["routing_key"] == ""


# ------------------------------
# start_consumer helpers
# ------------------------------

class FakeAsyncIterator:
    def __init__(self, messages=None):
        self._messages = list(messages or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


class FakeMessage:
    def __init__(self, body: dict | bytes, routing_key: str = "rk"):
        self.routing_key = routing_key
        if isinstance(body, dict):
            self.body = json.dumps(body).encode()
        else:
            self.body = body

    def process(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


# ------------------------------
# start_consumer tests
# ------------------------------

@pytest.mark.asyncio
async def test_start_consumer_topic():
    fake_queue = AsyncMock()
    fake_channel = AsyncMock()
    fake_channel.declare_queue.return_value = fake_queue
    fake_connection = AsyncMock()
    fake_connection.channel.return_value = fake_channel
    fake_exchange = AsyncMock()

    fake_message = FakeMessage({"foo": "bar"})
    fake_queue.iterator = lambda: FakeAsyncIterator([fake_message])

    called = {}
    done = asyncio.Event()

    async def handler(payload, rk):
        called["payload"] = payload
        called["rk"] = rk
        done.set()

    task = asyncio.create_task(
        start_consumer(fake_connection, fake_exchange, aio_pika.ExchangeType.TOPIC,
                       queue_name="q", patterns=["rk"], handler=handler)
    )

    await asyncio.wait_for(done.wait(), timeout=1)
    task.cancel()

    fake_queue.bind.assert_awaited_with(fake_exchange, routing_key="rk")
    assert called["payload"] == {"foo": "bar"}
    assert called["rk"] == "rk"


@pytest.mark.asyncio
async def test_start_consumer_fanout():
    fake_queue = AsyncMock()
    fake_channel = AsyncMock()
    fake_channel.declare_queue.return_value = fake_queue
    fake_connection = AsyncMock()
    fake_connection.channel.return_value = fake_channel
    fake_exchange = AsyncMock()

    async def handler(payload, rk): ...

    fake_queue.iterator = lambda: FakeAsyncIterator([])

    task = asyncio.create_task(
        start_consumer(fake_connection, fake_exchange, aio_pika.ExchangeType.FANOUT,
                       queue_name="q", patterns=[], handler=handler)
    )
    await asyncio.sleep(0.05)
    task.cancel()

    fake_queue.bind.assert_awaited_with(fake_exchange, routing_key="")


@pytest.mark.asyncio
async def test_start_consumer_bad_json(caplog):
    fake_queue = AsyncMock()
    fake_channel = AsyncMock()
    fake_channel.declare_queue.return_value = fake_queue
    fake_connection = AsyncMock()
    fake_connection.channel.return_value = fake_channel
    fake_exchange = AsyncMock()

    bad_msg = FakeMessage(b"\x80notjson")
    fake_queue.iterator = lambda: FakeAsyncIterator([bad_msg])

    called = {}
    done = asyncio.Event()

    async def handler(payload, rk):
        called.update(payload)
        done.set()

    task = asyncio.create_task(
        start_consumer(fake_connection, fake_exchange, aio_pika.ExchangeType.TOPIC,
                       queue_name="q", patterns=["rk"], handler=handler)
    )

    await asyncio.wait_for(done.wait(), timeout=1)
    task.cancel()

    assert "raw" in called


@pytest.mark.asyncio
async def test_start_consumer_handler_exception(caplog):
    fake_queue = AsyncMock()
    fake_channel = AsyncMock()
    fake_channel.declare_queue.return_value = fake_queue
    fake_connection = AsyncMock()
    fake_connection.channel.return_value = fake_channel
    fake_exchange = AsyncMock()

    good_msg = FakeMessage({"ok": True})
    fake_queue.iterator = lambda: FakeAsyncIterator([good_msg])

    async def handler(payload, rk):
        raise RuntimeError("boom")

    caplog.set_level(logging.ERROR)
    task = asyncio.create_task(
        start_consumer(fake_connection, fake_exchange, aio_pika.ExchangeType.TOPIC,
                       queue_name="q", patterns=["rk"], handler=handler)
    )
    await asyncio.sleep(0.05)
    task.cancel()

    assert "Handler error rk=" in caplog.text


# ------------------------------
# Misc: exchange type map
# ------------------------------

def test_exchange_type_map_defaults(monkeypatch):
    # Cas invalide => fallback en TOPIC
    monkeypatch.setattr("app.infra.events.rabbitmq.settings.RABBITMQ_EXCHANGE_TYPE", "invalid-type")
    mq = RabbitMQ()
    assert mq.exchange_type == aio_pika.ExchangeType.TOPIC

# tests/test_core.py
import os
import tempfile
import logging
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock

import sqlalchemy

from app.core import config, database, logging as core_logging


# ---------- config.py ----------

def test_get_bool_and_int(monkeypatch):
    monkeypatch.setenv("BOOL_TRUE", "yes")
    monkeypatch.setenv("BOOL_FALSE", "no")
    assert config._get_bool("BOOL_TRUE") is True
    assert config._get_bool("BOOL_FALSE") is False
    assert config._get_bool("MISSING", True) is True

    monkeypatch.setenv("INT_OK", "42")
    monkeypatch.setenv("INT_BAD", "oops")
    assert config._get_int("INT_OK", 1) == 42
    assert config._get_int("INT_BAD", 1) == 1


def test_compose_db_url_postgres(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_DB", "db")
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    s = config.Settings()
    assert s.DATABASE_URL.startswith("postgresql+")


def test_compose_db_url_sqlite(tmp_path, monkeypatch):
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "file.db"))
    s = config.Settings()
    assert s.DATABASE_URL.startswith("sqlite:///")


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    s = config.Settings()
    assert isinstance(s.APP_NAME, str)
    assert isinstance(s.ROLE_READ, str)
    assert s.RABBITMQ_EXCHANGE_TYPE in ("topic", "fanout", "direct", "headers")


# ---------- database.py ----------

def test_engine_and_session():
    # Vérifie que l’engine est créé
    eng = database.engine
    assert isinstance(eng.url, sqlalchemy.engine.url.URL)
    sess = database.SessionLocal()
    sess.close()


def test_init_db(monkeypatch):
    called = {}
    monkeypatch.setattr(database.Base.metadata, "create_all", lambda bind: called.setdefault("ok", True))
    database.init_db()
    assert called["ok"] is True


def test_get_db_success_and_exception(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(database, "SessionLocal", lambda: db)

    # cas normal
    gen = database.get_db()
    next(gen)  # ouvre
    with pytest.raises(StopIteration):
        gen.send(None)

    # cas exception
    def boom():
        raise ValueError("fail")
        yield
    db.rollback = MagicMock()
    gen = database.get_db()
    next(gen)
    with pytest.raises(ValueError):
        gen.throw(ValueError("fail"))


# ---------- logging.py ----------

def test_context_filter_and_secrets_filter(monkeypatch):
    f = core_logging.ContextFilter("svc")
    record = logging.LogRecord("n", logging.INFO, "", 1, "msg", (), None)
    assert f.filter(record)

    s = core_logging.SecretsFilter()
    record = logging.LogRecord("n", logging.INFO, "", 1,
                               'Authorization Bearer abc.def.ghi {"password":"x"}', (), None)
    s.filter(record)
    assert "[REDACTED]" in record.msg


def test_json_and_plain_formatter():
    rec = logging.LogRecord("n", logging.INFO, "", 1, "hello", (), None)
    j = core_logging.JsonFormatter()
    out = j.format(rec)
    assert "hello" in out

    p = core_logging.PlainFormatter("%(message)s")
    out = p.format(rec)
    assert "hello" in out


def test_setup_logging_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(config.settings, "LOG_FORMAT", "plain")
    monkeypatch.setattr(config.settings, "LOG_ENABLE_CONSOLE", True)

    core_logging.setup_logging()
    logger = logging.getLogger()
    assert any(isinstance(h, logging.Handler) for h in logger.handlers)

    # second appel: idempotent
    core_logging.setup_logging()
    assert getattr(logger, "_configured", False) is True


@pytest.mark.asyncio
async def test_access_log_middleware_success():
    class DummyRequest:
        method = "GET"
        url = SimpleNamespace(path="/x")
        client = SimpleNamespace(host="1.2.3.4")
        headers = {"user-agent": "UA"}

    async def call_next(req):
        return SimpleNamespace(status_code=200, headers={})

    req = DummyRequest()
    resp = await core_logging.access_log_middleware(req, call_next)
    assert resp.headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_access_log_middleware_exception():
    class DummyRequest:
        method = "GET"
        url = SimpleNamespace(path="/err")
        client = SimpleNamespace(host="1.2.3.4")
        headers = {"user-agent": "UA"}
        headers = {}

    async def call_next(req):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await core_logging.access_log_middleware(DummyRequest(), call_next)

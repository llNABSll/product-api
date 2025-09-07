import pytest
from fastapi.testclient import TestClient

import app.main


@pytest.fixture
def client(monkeypatch):
    # Patch rabbitmq connect/disconnect pour ne pas dépendre d'une vraie infra
    async def fake_connect(): return None
    async def fake_disconnect(): return None
    monkeypatch.setattr(app.main.rabbitmq, "connect", fake_connect)
    monkeypatch.setattr(app.main.rabbitmq, "disconnect", fake_disconnect)
    return TestClient(app.main.app)


def test_health_ok(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_metrics_exposed(client):
    res = client.get("/metrics")
    assert res.status_code == 200
    assert "http_requests_total" in res.text


def test_prometheus_counter_increment(client):
    # Appel d'une route pour générer des métriques
    client.get("/health")
    res = client.get("/metrics")
    assert "http_request_duration_seconds" in res.text


def test_lifespan_runs(monkeypatch):
    called = {}

    async def fake_connect():
        called["connect"] = True

    async def fake_disconnect():
        called["disconnect"] = True

    monkeypatch.setattr(app.main.rabbitmq, "connect", fake_connect)
    monkeypatch.setattr(app.main.rabbitmq, "disconnect", fake_disconnect)

    with TestClient(app.main.app) as client:
        res = client.get("/health")
        assert res.status_code == 200

    # après fermeture du client → disconnect appelé
    assert "connect" in called
    assert "disconnect" in called

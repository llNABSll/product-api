import pytest
from unittest.mock import AsyncMock, MagicMock

import app.infra.events.handlers as handlers
from app.services import product_service


# ============================
# ORDER CREATED
# ============================

@pytest.mark.asyncio
async def test_handle_order_created_success(monkeypatch):
    fake_svc = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {
        "id": 1,
        "customer_id": 42,
        "items": [{"product_id": 101, "quantity": 2}]
    }

    await handlers.handle_order_created(payload, db=MagicMock())

    fake_svc.adjust_stock.assert_awaited_once_with(101, -2)


@pytest.mark.asyncio
async def test_handle_order_created_insufficient_stock(monkeypatch, caplog):
    fake_svc = AsyncMock()
    fake_svc.adjust_stock.side_effect = product_service.InsufficientStockError()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {"id": 1, "items": [{"product_id": 101, "quantity": 5}]}

    await handlers.handle_order_created(payload, db=MagicMock())

    fake_svc.adjust_stock.assert_awaited()
    assert "Stock insuffisant produit 101" in caplog.text


@pytest.mark.asyncio
async def test_handle_order_created_empty_payload(monkeypatch):
    fake_svc = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {}  # pas d'items
    await handlers.handle_order_created(payload, db=MagicMock())

    fake_svc.adjust_stock.assert_not_called()


# ============================
# ORDER DELETED
# ============================

@pytest.mark.asyncio
async def test_handle_order_deleted(monkeypatch):
    fake_svc = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {"id": 1, "items": [{"product_id": 101, "quantity": 3}]}

    await handlers.handle_order_deleted(payload, db=MagicMock())

    fake_svc.adjust_stock.assert_awaited_once_with(101, 3)


@pytest.mark.asyncio
async def test_handle_order_deleted_empty_payload(monkeypatch):
    fake_svc = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {}  # pas d'items
    await handlers.handle_order_deleted(payload, db=MagicMock())

    fake_svc.adjust_stock.assert_not_called()


# ============================
# ORDER UPDATED
# ============================

@pytest.mark.asyncio
async def test_handle_order_updated_cancelled(monkeypatch):
    fake_svc = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {
        "id": 1,
        "status": "cancelled",
        "items": [{"product_id": 101, "quantity": 4}]
    }

    await handlers.handle_order_updated(payload, db=MagicMock())

    fake_svc.adjust_stock.assert_awaited_once_with(101, 4)


@pytest.mark.asyncio
async def test_handle_order_updated_other_status(monkeypatch, caplog):
    fake_svc = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {"id": 1, "status": "completed", "items": []}

    await handlers.handle_order_updated(payload, db=MagicMock())

    fake_svc.adjust_stock.assert_not_called()
    assert "Pas d’ajustement pour statut completed" in caplog.text


@pytest.mark.asyncio
async def test_handle_order_updated_no_status(monkeypatch, caplog):
    fake_svc = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {"id": 1}  # pas de status ni items
    await handlers.handle_order_updated(payload, db=MagicMock())

    fake_svc.adjust_stock.assert_not_called()
    assert "Pas d’ajustement pour statut None" in caplog.text


# ============================
# _get_service factory
# ============================

def test__get_service_returns_product_service():
    from sqlalchemy.orm import Session
    svc = handlers._get_service(MagicMock(spec=Session))
    assert isinstance(svc, product_service.ProductService)

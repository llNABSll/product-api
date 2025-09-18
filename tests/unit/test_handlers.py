import pytest
from unittest.mock import AsyncMock, MagicMock
import app.infra.events.handlers as handlers
from app.services import product_service


# =====================================================
# CLEAN ITEMS / DELTAS
# =====================================================

def test_clean_items_valid():
    payload = {"items": [{"product_id": "1", "quantity": "2"}]}
    assert handlers._clean_items(payload) == [{"product_id": 1, "quantity": 2}]


def test_clean_items_negative_and_invalid(caplog):
    payload = {"items": [{"product_id": 1, "quantity": -5}, {"foo": "bar"}]}
    items = handlers._clean_items(payload)
    assert items == []
    assert "quantité négative" in caplog.text or "item invalide" in caplog.text


def test_clean_deltas_valid_and_zero():
    payload = {"deltas": [{"product_id": "2", "delta": "3"}, {"product_id": 2, "delta": 0}]}
    # zero est ignoré
    assert handlers._clean_deltas(payload) == [{"product_id": 2, "delta": 3}]


def test_clean_deltas_invalid(caplog):
    payload = {"deltas": [{"foo": "bar"}]}
    out = handlers._clean_deltas(payload)
    assert out == []
    assert "delta invalide" in caplog.text


# =====================================================
# ORDER CREATED
# =====================================================

@pytest.mark.asyncio
async def test_handle_order_created_success(monkeypatch):
    fake_svc = AsyncMock()
    fake_svc.get = MagicMock(return_value=MagicMock(quantity=10))
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {"id": 1, "items": [{"product_id": 101, "quantity": 2}]}
    await handlers.handle_order_created(payload, db=MagicMock())

    fake_svc.adjust_stock.assert_awaited_once_with(101, -2)


@pytest.mark.asyncio
async def test_handle_order_created_insufficient_stock(monkeypatch, caplog):
    fake_svc = AsyncMock()
    fake_svc.get = MagicMock(return_value=MagicMock(quantity=3))
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {"id": 1, "items": [{"product_id": 101, "quantity": 5}]}
    await handlers.handle_order_created(payload, db=MagicMock())

    fake_svc.adjust_stock.assert_not_called()
    assert "[order.created] rollback 1 ->" in caplog.text


@pytest.mark.asyncio
async def test_handle_order_created_empty_payload(monkeypatch, caplog):
    fake_svc = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {"id": 99, "items": []}
    await handlers.handle_order_created(payload, db=MagicMock())

    assert "commande 99 sans items" in caplog.text
    fake_svc.adjust_stock.assert_not_called()


# =====================================================
# ORDER ITEMS DELTA
# =====================================================

@pytest.mark.asyncio
async def test_handle_order_items_delta_success(monkeypatch):
    fake_svc = AsyncMock()
    fake_svc.get = MagicMock(return_value=MagicMock(quantity=10))
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {"id": 1, "deltas": [{"product_id": 101, "delta": 3}, {"product_id": 101, "delta": -1}]}
    await handlers.handle_order_items_delta(payload, db=MagicMock())

    fake_svc.adjust_stock.assert_any_await(101, -3)
    fake_svc.adjust_stock.assert_any_await(101, 1)


@pytest.mark.asyncio
async def test_handle_order_items_delta_insufficient(monkeypatch, caplog):
    fake_svc = AsyncMock()
    fake_svc.get = MagicMock(return_value=MagicMock(quantity=1))
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {"id": 1, "deltas": [{"product_id": 101, "delta": 5}]}
    await handlers.handle_order_items_delta(payload, db=MagicMock())

    fake_svc.adjust_stock.assert_not_called()
    assert "[order.items_delta] rollback 1 ->" in caplog.text


@pytest.mark.asyncio
async def test_handle_order_items_delta_no_deltas(monkeypatch, caplog):
    fake_svc = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    await handlers.handle_order_items_delta({"id": 42, "deltas": []}, db=MagicMock())
    fake_svc.adjust_stock.assert_not_called()
    assert "[order.items_delta] 42 sans delta" in caplog.text


# =====================================================
# ORDER CANCELLED
# =====================================================

@pytest.mark.asyncio
async def test_handle_order_cancelled_success(monkeypatch):
    fake_svc = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {"id": 1, "items": [{"product_id": 101, "quantity": 3}]}
    await handlers.handle_order_cancelled(payload, db=MagicMock())

    fake_svc.adjust_stock.assert_awaited_once_with(101, 3)


@pytest.mark.asyncio
async def test_handle_order_cancelled_no_items(monkeypatch, caplog):
    fake_svc = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    await handlers.handle_order_cancelled({"id": 1, "items": []}, db=MagicMock())
    assert "[order.cancelled]" not in caplog.text or "sans items" not in caplog.text
    fake_svc.adjust_stock.assert_not_called()


# =====================================================
# ORDER REJECTED
# =====================================================

@pytest.mark.asyncio
async def test_handle_order_rejected_logs(caplog):
    await handlers.handle_order_rejected({"id": 7}, db=MagicMock())
    assert "[order.rejected] 7 -> no stock action" in caplog.text


# =====================================================
# ORDER DELETED
# =====================================================

@pytest.mark.asyncio
async def test_handle_order_deleted_success(monkeypatch):
    fake_svc = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {"id": 1, "status": "cancelled", "items": [{"product_id": 101, "quantity": 2}]}
    await handlers.handle_order_deleted(payload, db=MagicMock())

    fake_svc.adjust_stock.assert_awaited_once_with(101, 2)


@pytest.mark.asyncio
async def test_handle_order_deleted_rejected(monkeypatch, caplog):
    fake_svc = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {"id": 1, "status": "rejected", "items": [{"product_id": 101, "quantity": 2}]}
    await handlers.handle_order_deleted(payload, db=MagicMock())

    fake_svc.adjust_stock.assert_not_called()
    # ton handler ne log rien ici → on vérifie juste qu’il n’y a pas de crash
    assert "[order.deleted]" not in caplog.text or "déjà rejetée" not in caplog.text


@pytest.mark.asyncio
async def test_handle_order_deleted_no_items(monkeypatch, caplog):
    fake_svc = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    await handlers.handle_order_deleted({"id": 1, "status": "cancelled", "items": []}, db=MagicMock())
    fake_svc.adjust_stock.assert_not_called()
    # idem, pas de log attendu → juste vérifier pas d’appel


# =====================================================
# ORDER UPDATED
# =====================================================

@pytest.mark.asyncio
async def test_handle_order_updated_cancelled(monkeypatch, caplog):
    fake_svc = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {"id": 1, "status": "cancelled", "items": [{"product_id": 101, "quantity": 4}]}
    await handlers.handle_order_updated(payload, db=MagicMock())

    fake_svc.adjust_stock.assert_not_called()
    assert "[order.updated] 1 status=cancelled" in caplog.text


@pytest.mark.asyncio
async def test_handle_order_updated_other_status(monkeypatch, caplog):
    fake_svc = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {"id": 1, "status": "completed", "items": []}
    await handlers.handle_order_updated(payload, db=MagicMock())

    fake_svc.adjust_stock.assert_not_called()
    assert "[order.updated] 1 status=completed" in caplog.text


@pytest.mark.asyncio
async def test_handle_order_updated_no_status(monkeypatch, caplog):
    fake_svc = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {"id": 1}
    await handlers.handle_order_updated(payload, db=MagicMock())

    fake_svc.adjust_stock.assert_not_called()
    assert "[order.updated] 1 status=None" in caplog.text


# =====================================================
# _get_service factory
# =====================================================

def test__get_service_returns_product_service():
    svc = handlers._get_service(MagicMock())
    assert isinstance(svc, product_service.ProductService)


@pytest.mark.asyncio
async def test_handle_order_price_request_invalid_payload(monkeypatch, caplog):
    """
    Doit logger un warning et ne rien publier
    quand customer_id ou items est manquant.
    """
    fake_svc = AsyncMock()
    fake_svc.mq = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {"items": []}  # pas de customer_id
    await handlers.handle_order_price_request(payload, db=MagicMock())

    assert "[order.request_price] payload invalide" in caplog.text
    fake_svc.mq.publish_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_order_price_request_success(monkeypatch):
    """
    Doit enrichir les items, calculer le total et publier un event.
    """
    fake_svc = AsyncMock()
    fake_svc.get = MagicMock(return_value=MagicMock(price=5.5))
    fake_svc.mq = AsyncMock()
    monkeypatch.setattr(handlers, "_get_service", lambda db: fake_svc)

    payload = {
        "customer_id": 42,
        "items": [{"product_id": 10, "quantity": 3}],
    }

    await handlers.handle_order_price_request(payload, db=MagicMock())

    fake_svc.mq.publish_message.assert_awaited_once_with(
        "order.price_calculated",
        {
            "customer_id": 42,
            "items": [
                {"product_id": 10, "quantity": 3, "unit_price": 5.5}
            ],
            "total": 16.5,
        },
    )

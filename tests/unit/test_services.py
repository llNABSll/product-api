# tests/unit/test_services.py
import pytest
from unittest.mock import MagicMock
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.services.product_service import (
    ProductService,
    NotFoundError,
    SKUAlreadyExistsError,
    ConcurrencyConflictError,
    InsufficientStockError,
)
from app.models.product import Product
from app.schemas.product_schema import ProductCreate, ProductUpdate


# ---------- Fixtures ----------
@pytest.fixture
def fake_db():
    return MagicMock()

@pytest.fixture
def fake_mq():
    """Mock RabbitMQ ignoré dans les tests unitaires"""
    return MagicMock()

@pytest.fixture
def product():
    return Product(
        id=1, sku="SKU1", name="Test",
        price=10.0, quantity=5, version=1, is_active=True
    )


# ---------- GET ----------
def test_get_found(fake_db, fake_mq, product):
    fake_db.get.return_value = product
    svc = ProductService(fake_db, fake_mq)
    assert svc.get(1).sku == "SKU1"

def test_get_not_found(fake_db, fake_mq):
    fake_db.get.return_value = None
    svc = ProductService(fake_db, fake_mq)
    with pytest.raises(NotFoundError):
        svc.get(123)


# ---------- CREATE ----------
@pytest.mark.asyncio
async def test_create_ok(fake_db, fake_mq):
    fake_db.execute().scalars().first.return_value = None
    fake_db.add.side_effect = lambda obj: setattr(obj, "id", 42)

    svc = ProductService(fake_db, fake_mq)
    created = await svc.create(ProductCreate(sku="S1", name="X", price=10, quantity=1))

    assert created.id == 42

@pytest.mark.asyncio
async def test_create_conflict_existing(fake_db, fake_mq, product):
    fake_db.execute().scalars().first.return_value = product
    svc = ProductService(fake_db, fake_mq)
    with pytest.raises(SKUAlreadyExistsError):
        await svc.create(ProductCreate(sku="SKU1", name="Dup", price=10, quantity=1))


# ---------- UPDATE ----------
@pytest.mark.asyncio
async def test_update_ok(fake_db, fake_mq, product):
    fake_db.get.return_value = product
    svc = ProductService(fake_db, fake_mq)

    updated = await svc.update(1, ProductUpdate(name="New"))
    assert updated.name == "New"

@pytest.mark.asyncio
async def test_update_not_found(fake_db, fake_mq):
    fake_db.get.return_value = None
    svc = ProductService(fake_db, fake_mq)
    with pytest.raises(NotFoundError):
        await svc.update(1, ProductUpdate(name="X"))

@pytest.mark.asyncio
async def test_update_version_conflict(fake_db, fake_mq, product):
    product.version = 2
    fake_db.get.return_value = product
    svc = ProductService(fake_db, fake_mq)
    with pytest.raises(ConcurrencyConflictError):
        await svc.update(1, ProductUpdate(name="X"), expected_version=1)

@pytest.mark.asyncio
async def test_update_integrity_error(fake_db, fake_mq, product):
    fake_db.get.return_value = product
    fake_db.commit.side_effect = IntegrityError("msg", "params", "orig")
    svc = ProductService(fake_db, fake_mq)
    with pytest.raises(SKUAlreadyExistsError):
        await svc.update(1, ProductUpdate(name="X"))

@pytest.mark.asyncio
async def test_update_staledata_error(fake_db, fake_mq, product):
    fake_db.get.return_value = product
    fake_db.commit.side_effect = StaleDataError()
    svc = ProductService(fake_db, fake_mq)
    with pytest.raises(ConcurrencyConflictError):
        await svc.update(1, ProductUpdate(name="X"))


# ---------- DELETE ----------
@pytest.mark.asyncio
async def test_delete_ok(fake_db, fake_mq, product):
    fake_db.get.return_value = product
    svc = ProductService(fake_db, fake_mq)
    deleted = await svc.delete(1)
    assert deleted.sku == "SKU1"

@pytest.mark.asyncio
async def test_delete_not_found(fake_db, fake_mq):
    fake_db.get.return_value = None
    svc = ProductService(fake_db, fake_mq)
    with pytest.raises(NotFoundError):
        await svc.delete(1)


# ---------- ADJUST STOCK ----------
@pytest.mark.asyncio
async def test_adjust_stock_negative(fake_db, fake_mq, product):
    product.quantity = 0
    fake_db.get.return_value = product
    svc = ProductService(fake_db, fake_mq)
    with pytest.raises(InsufficientStockError):
        await svc.adjust_stock(1, -1)

@pytest.mark.asyncio
async def test_adjust_stock_ok(fake_db, fake_mq, product):
    fake_db.get.return_value = product
    old_qty = product.quantity
    svc = ProductService(fake_db, fake_mq)
    updated = await svc.adjust_stock(1, +5)
    assert updated.quantity == old_qty + 5


# ---------- SET ACTIVE ----------
@pytest.mark.asyncio
async def test_set_active_true(fake_db, fake_mq, product):
    fake_db.get.return_value = product
    svc = ProductService(fake_db, fake_mq)
    updated = await svc.set_active(1, True)
    assert updated.is_active is True

@pytest.mark.asyncio
async def test_set_active_false(fake_db, fake_mq, product):
    fake_db.get.return_value = product
    svc = ProductService(fake_db, fake_mq)
    updated = await svc.set_active(1, False)
    assert updated.is_active is False


# ---------- UPSERT ----------
@pytest.mark.asyncio
async def test_upsert_create(fake_db, fake_mq):
    fake_db.execute().scalars().first.return_value = None
    svc = ProductService(fake_db, fake_mq)
    created = await svc.upsert_by_sku(ProductCreate(sku="UP", name="Upsert", price=1.0, quantity=1))
    assert created.sku == "UP"

@pytest.mark.asyncio
async def test_upsert_update(fake_db, fake_mq, product):
    fake_db.execute().scalars().first.return_value = product
    svc = ProductService(fake_db, fake_mq)
    updated = await svc.upsert_by_sku(ProductCreate(sku="SKU1", name="Upserted", price=1.0, quantity=1))
    assert updated.sku == "SKU1"

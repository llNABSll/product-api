# tests/test_api.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock
from datetime import datetime, timezone

from app.main import app
from app.services import product_service
from app.security import security
import app.api.routes.product as product_routes
from app.schemas.product_schema import ProductResponse

# This client fixture will be used by all tests in this file.
# It handles all the setup and teardown for each test.
@pytest.fixture
def client(patch_rabbitmq):
    # 1. Create a mock for the ProductService
    mock_svc = AsyncMock(spec=product_service.ProductService)
    
    fake_product = ProductResponse(
        id=1, sku="S1", name="Prod", description="Desc", unit="piece",
        brand="BrandX", category="CategoryY", price=10.0, quantity=5,
        vat_rate=0.2, version=1, is_active=True,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)
    )

    mock_svc.get.return_value = fake_product
    mock_svc.list.return_value = [fake_product]
    mock_svc.get_by_sku.return_value = fake_product
    mock_svc.create.return_value = fake_product
    mock_svc.update.return_value = fake_product
    mock_svc.delete.return_value = fake_product
    mock_svc.adjust_stock.return_value = fake_product
    mock_svc.set_active.return_value = fake_product
    mock_svc.upsert_by_sku.return_value = fake_product

    # 2. Create a fake user context for security
    fake_user_context = security.AuthContext(
        user="tester",
        email="tester@example.com",
        roles=["product:read", "product:write"],
    )

    # 3. Apply the dependency overrides to the app
    app.dependency_overrides = {
        product_routes.get_product_service: lambda: mock_svc,
        security.require_user: lambda: fake_user_context,
        security.require_read: lambda: fake_user_context,
        security.require_write: lambda: fake_user_context,
    }

    # 4. Yield the TestClient for the test to run
    yield TestClient(app)

    # 5. Clean up the overrides after the test is done
    app.dependency_overrides = {}


# ---- Tests ----
# All tests now take the `client` fixture as an argument.

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_metrics(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "http_requests_total" in r.text


def test_create_product(client):
    r = client.post("/products/", json={"sku": "S1", "name": "X", "price": 10.0, "quantity": 1})
    assert r.status_code == 201
    # We can access the mock through the dependency override if needed
    mock_service = app.dependency_overrides[product_routes.get_product_service]()
    mock_service.create.assert_awaited()


def test_list_products(client):
    r = client.get("/products/")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_read_product(client):
    r = client.get("/products/1")
    assert r.status_code == 200
    mock_service = app.dependency_overrides[product_routes.get_product_service]()
    mock_service.get.assert_called_with(1)


def test_read_not_found(client):
    mock_service = app.dependency_overrides[product_routes.get_product_service]()
    mock_service.get.side_effect = product_service.NotFoundError()
    r = client.get("/products/99")
    assert r.status_code == 404


def test_update_product(client):
    r = client.put("/products/1", json={"name": "Updated"})
    assert r.status_code == 200
    mock_service = app.dependency_overrides[product_routes.get_product_service]()
    mock_service.update.assert_awaited()


def test_update_conflict(client):
    mock_service = app.dependency_overrides[product_routes.get_product_service]()
    mock_service.update.side_effect = product_service.ConcurrencyConflictError()
    r = client.put("/products/1", json={"name": "Updated"})
    assert r.status_code == 409


def test_delete_product(client):
    r = client.delete("/products/1")
    assert r.status_code == 200
    mock_service = app.dependency_overrides[product_routes.get_product_service]()
    mock_service.delete.assert_awaited()


def test_delete_not_found(client):
    mock_service = app.dependency_overrides[product_routes.get_product_service]()
    mock_service.delete.side_effect = product_service.NotFoundError()
    r = client.delete("/products/1")
    assert r.status_code == 404


def test_read_by_sku(client):
    r = client.get("/products/sku/S1")
    assert r.status_code == 200
    mock_service = app.dependency_overrides[product_routes.get_product_service]()
    mock_service.get_by_sku.assert_called()


def test_adjust_stock(client):
    r = client.patch("/products/1/stock?delta=5")
    assert r.status_code == 200
    mock_service = app.dependency_overrides[product_routes.get_product_service]()
    mock_service.adjust_stock.assert_awaited()


def test_set_active(client):
    r = client.patch("/products/1/active?is_active=false")
    assert r.status_code == 200
    mock_service = app.dependency_overrides[product_routes.get_product_service]()
    mock_service.set_active.assert_awaited()
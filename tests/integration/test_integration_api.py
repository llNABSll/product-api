# tests/integration/test_api.py
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import Base, engine
from app.security.security import AuthContext, require_user, require_read, require_write

pytestmark = pytest.mark.integration

# ---------------------------
# DB setup (SQLite in-memory)
# ---------------------------
@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


# ---------------------------
# Patch RabbitMQ + Security
# ---------------------------
@pytest.fixture
def client(patch_rabbitmq):
    # Fake sécurité : AuthContext toujours accepté
    fake_ctx = AuthContext(
        user="test-user",
        email="test@example.com",
        roles=["product:write", "product:read"],
    )
    app.dependency_overrides[require_user] = lambda: fake_ctx
    app.dependency_overrides[require_read] = lambda: fake_ctx
    app.dependency_overrides[require_write] = lambda: fake_ctx

    yield TestClient(app)

    # Nettoyage overrides
    app.dependency_overrides.clear()


# ---------------------------
# Tests API produits
# ---------------------------
def test_create_and_get_product(client):
    payload = {"sku": "SKU1", "name": "Test", "price": 10.0, "quantity": 5}
    res = client.post("/products/", json=payload)
    assert res.status_code == 201
    product = res.json()
    assert product["sku"] == "SKU1"

    # Lecture par ID
    res2 = client.get(f"/products/{product['id']}")
    assert res2.status_code == 200
    assert res2.json()["name"] == "Test"


def test_conflict_on_duplicate_sku(client):
    payload = {"sku": "SKU_DUP", "name": "Dup", "price": 5.0, "quantity": 1}
    client.post("/products/", json=payload)
    res = client.post("/products/", json=payload)
    assert res.status_code == 409


def test_list_and_filters(client):
    client.post("/products/", json={"sku": "S1", "name": "N1", "price": 1, "quantity": 1})
    client.post("/products/", json={"sku": "S2", "name": "N2", "price": 2, "quantity": 2})
    res = client.get("/products/?min_price=2")
    assert res.status_code == 200
    rows = res.json()
    assert all(p["price"] >= 2 for p in rows)

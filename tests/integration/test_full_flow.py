import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import Base, engine
from app.security.security import AuthContext, require_read, require_write

pytestmark = pytest.mark.acceptance


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(patch_rabbitmq):
    # Mock Security Dependencies
    fake_ctx = AuthContext(
        user="test-user",
        email="test@example.com",
        roles=["product:read", "product:write"],
    )
    app.dependency_overrides[require_read] = lambda: fake_ctx
    app.dependency_overrides[require_write] = lambda: fake_ctx

    yield TestClient(app)

    # Clean up
    app.dependency_overrides.clear()


def test_full_product_lifecycle(client):
    # 1. Création
    payload = {"sku": "LIFE1", "name": "Lifecycle", "price": 50.0, "quantity": 10}
    res = client.post("/products/", json=payload)
    assert res.status_code == 201
    prod = res.json()
    pid = prod["id"]

    # 2. Update
    res2 = client.put(f"/products/{pid}", json={"name": "Lifecycle Updated"})
    assert res2.status_code == 200
    assert res2.json()["name"] == "Lifecycle Updated"

    # 3. Ajustement de stock (delta dans le body)
    res3 = client.patch(f"/products/{pid}/stock", json={"delta": -2})
    assert res3.status_code == 200
    assert res3.json()["quantity"] == 8

    # 4. Désactivation (is_active dans le body)
    res4 = client.patch(f"/products/{pid}/active", json={"is_active": False})
    assert res4.status_code == 200
    assert res4.json()["is_active"] is False

    # 5. Suppression
    res5 = client.delete(f"/products/{pid}")
    assert res5.status_code == 200
    res6 = client.get(f"/products/{pid}")
    assert res6.status_code == 404
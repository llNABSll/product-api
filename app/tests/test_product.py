# app/tests/test_products_full.py
from __future__ import annotations

import pytest
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# --- IMPORTANT : préparer la DB test & monkeypatch avant le TestClient ---

@pytest.fixture(scope="session")
def test_engine(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("db") / "test_api.sqlite"
    return create_engine(f"sqlite:///{db_path}", future=True)

@pytest.fixture(scope="session")
def TestingSessionLocal(test_engine):
    return sessionmaker(bind=test_engine, autoflush=False, autocommit=False, future=True)

@pytest.fixture(scope="session", autouse=True)
def app_modules_patched(test_engine, TestingSessionLocal):
    """
    Patch du moteur/SessionLocal, enregistrement du schéma, et mock RabbitMQ.
    """
    # 1) brancher l'engine/SessionLocal de test
    import app.core.database as db_module
    db_module.engine = test_engine
    db_module.SessionLocal = TestingSessionLocal

    # 2) enregistrer les modèles AVANT create_all
    import app.models.product  # noqa: F401

    # 3) créer le schéma
    from app.core.database import Base
    Base.metadata.create_all(bind=test_engine)

    # 4) Monkeypatch RabbitMQ : no-op + capture des events
    import app.core.rabbitmq as mq
    events: list[dict] = []

    async def _noop_connect(): return None
    async def _noop_disconnect(): return None
    async def _capture_publish_json(exchange: str, payload: dict):
        events.append({"exchange": exchange, **payload})

    mq.rabbitmq.connect = _noop_connect          # type: ignore
    mq.rabbitmq.disconnect = _noop_disconnect    # type: ignore
    mq.rabbitmq.publish_json = _capture_publish_json  # type: ignore
    mq._captured_events = events  # type: ignore

    yield
    # teardown optionnel

@pytest.fixture(autouse=True)
def _clean_db_between_tests(test_engine):
    """Reset de la table entre chaque test (ignore si pas encore créée)."""
    with test_engine.begin() as conn:
        try:
            conn.exec_driver_sql("DELETE FROM products")
        except Exception:
            pass  # première exécution avant create_all : on ignore

# --- Helpers auth pour tests (simulation JWT via dépendances FastAPI) ---

# On réutilise le schéma HTTPBearer pour extraire l'en-tête Authorization.
_http_bearer = HTTPBearer(auto_error=False)

def _fake_require_read(creds: HTTPAuthorizationCredentials = Security(_http_bearer)):
    """
    Lecture autorisée si Authorization est présent (Bearer READ ou Bearer WRITE).
    - Absence d'Authorization => 401
    - Présence => OK
    """
    if creds is None:
        raise HTTPException(status_code=401, detail="Authorization requis")
    token = creds.credentials or ""
    if token not in {"READ", "WRITE"}:
        # On simule un JWT invalide
        raise HTTPException(status_code=401, detail="JWT invalide")
    # Retourne un payload minimal (si un handler veut l'exploiter)
    return {"realm_access": {"roles": ["product:read"]}, "sub": "test-user"}

def _fake_require_write(creds: HTTPAuthorizationCredentials = Security(_http_bearer)):
    """
    Écriture autorisée uniquement avec Bearer WRITE.
    - Absence d'Authorization => 401
    - Authorization Bearer READ => 403
    - Authorization Bearer WRITE => OK
    """
    if creds is None:
        raise HTTPException(status_code=401, detail="Authorization requis")
    token = creds.credentials or ""
    if token == "WRITE":
        return {"realm_access": {"roles": ["product:write"]}, "sub": "admin-user"}
    if token == "READ":
        raise HTTPException(status_code=403, detail="Rôle requis: product:write")
    raise HTTPException(status_code=401, detail="JWT invalide")

@pytest.fixture()
def client():
    """
    Crée un TestClient avec surcharges de dépendances de sécurité.
    - Évite tout appel à Keycloak/JWKS.
    - Contrôle les statuts 401/403 via des tokens symboliques (READ/WRITE).
    """
    from app.main import app
    import app.security.security as sec

    # Dépendance override: remplace require_read / require_write par nos fakes
    app.dependency_overrides[sec.require_read] = _fake_require_read
    app.dependency_overrides[sec.require_write] = _fake_require_write

    with TestClient(app) as c:
        yield c

@pytest.fixture()
def events_log():
    import app.core.rabbitmq as mq
    mq._captured_events.clear()  # reset entre tests
    return mq._captured_events


# -------------------------- Utilitaires --------------------------

def h_read():
    # Simule un utilisateur "lecteur" (autorisé en GET, interdit en POST/PUT/DELETE)
    return {"Authorization": "Bearer READ"}

def h_admin():
    # Simule un utilisateur "admin" (autorisé partout)
    return {"Authorization": "Bearer WRITE"}

def mk_product(
    sku: str = "COF-ESP-250G",
    name: str = "Espresso",
    price: float = 7.9,
    quantity: int = 100,
    vat_rate: float = 0.2,
    **extras,
):
    payload = {
        "sku": sku,
        "name": name,
        "price": price,
        "quantity": quantity,
        "vat_rate": vat_rate,
        **extras,
    }
    return payload


# -------------------------- Tests de base --------------------------

def test_health(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

def test_metrics_endpoint(client: TestClient):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "http_requests_total" in r.text  # exposition Prometheus


# -------------------------- Sécurité (JWT simulé) --------------------------

def test_list_requires_bearer(client: TestClient):
    # Sans Authorization -> 401
    r = client.get("/products")
    assert r.status_code == 401

def test_write_requires_admin_role(client: TestClient):
    # Token lecteur (READ) -> 403 sur écriture
    r = client.post("/products", json=mk_product(), headers=h_read())
    assert r.status_code == 403

def test_invalid_token_for_read(client: TestClient):
    r = client.get("/products", headers={"Authorization": "Bearer NOPE"})
    assert r.status_code == 401


# -------------------------- CRUD + Events --------------------------

def test_create_list_get_update_delete_with_events(client: TestClient, events_log: list):
    # create (admin)
    p = mk_product(sku="COF-HOUSE-1KG", name="House Blend", price=5.5, quantity=30)
    r = client.post("/products", json=p, headers=h_admin())
    assert r.status_code == 201, r.text
    created = r.json()
    pid = created["id"]
    assert created["sku"] == "COF-HOUSE-1KG"

    # event created
    assert any(e.get("event") == "product.created" and e.get("id") == pid for e in events_log)

    # list (read)
    r = client.get("/products", headers=h_read())
    assert r.status_code == 200
    assert any(x["id"] == pid for x in r.json())

    # get one
    r = client.get(f"/products/{pid}", headers=h_read())
    assert r.status_code == 200
    assert r.json()["name"] == "House Blend"

    # update (admin)
    events_log.clear()
    r = client.put(f"/products/{pid}", json={"price": 6.2}, headers=h_admin())
    assert r.status_code == 200
    assert r.json()["price"] == 6.2
    # event updated
    assert any(e.get("event") == "product.updated" and e.get("id") == pid for e in events_log)

    # delete (admin)
    events_log.clear()
    r = client.delete(f"/products/{pid}", headers=h_admin())
    assert r.status_code == 200
    # get should now 404
    r = client.get(f"/products/{pid}", headers=h_read())
    assert r.status_code == 404
    # event deleted
    assert any(e.get("event") == "product.deleted" and e.get("id") == pid for e in events_log)


# -------------------------- Validations --------------------------

@pytest.mark.parametrize(
    "field,value",
    [
        ("price", -0.01),
        ("quantity", -1),
        ("vat_rate", -0.0001),
        ("vat_rate", 1.0001),
    ],
)
def test_create_invalid_numbers(client: TestClient, field, value):
    p = mk_product()
    p[field] = value
    r = client.post("/products", json=p, headers=h_admin())
    assert r.status_code == 422, r.text  # contraintes Pydantic

def test_create_extreme_values_ok(client: TestClient):
    # Très grand prix mais fini
    p = mk_product(sku="COF-BIG", price=1e308, quantity=10, vat_rate=1.0)
    r = client.post("/products", json=p, headers=h_admin())
    assert r.status_code == 201

def test_create_name_too_long_and_sku_too_long(client: TestClient):
    long_name = "N"*256
    long_sku = "S"*65
    p = mk_product(sku=long_sku, name=long_name)
    r = client.post("/products", json=p, headers=h_admin())
    assert r.status_code == 422  # dépasse les max_length

def test_quantity_wrong_type(client: TestClient):
    p = mk_product()
    p["quantity"] = "ten"
    r = client.post("/products", json=p, headers=h_admin())
    assert r.status_code == 422

def test_price_not_number_like_infinity_string_rejected(client: TestClient):
    # JSON -> "Infinity" (string) ne doit pas passer pour un float typé
    p = mk_product()
    p["price"] = "Infinity"
    r = client.post("/products", json=p, headers=h_admin())
    assert r.status_code == 422


# -------------------------- Unicité SKU & recherche SKU --------------------------

def test_duplicate_sku_conflict(client: TestClient):
    base = mk_product(sku="COF-ESP-250G")
    r1 = client.post("/products", json=base, headers=h_admin())
    assert r1.status_code == 201
    r2 = client.post("/products", json=base, headers=h_admin())
    assert r2.status_code == 409  # SKU déjà utilisé

def test_read_by_sku(client: TestClient):
    p = mk_product(sku="COF-MOCHA-500G", name="Mocha", price=6.1)
    client.post("/products", json=p, headers=h_admin())
    r = client.get("/products/sku/COF-MOCHA-500G", headers=h_read())
    assert r.status_code == 200
    assert r.json()["name"] == "Mocha"

def test_update_to_existing_sku_conflict(client: TestClient):
    a = mk_product(sku="COF-A", name="A")
    b = mk_product(sku="COF-B", name="B")
    id_a = client.post("/products", json=a, headers=h_admin()).json()["id"]
    client.post("/products", json=b, headers=h_admin())
    # tente de renommer A en sku de B
    r = client.put(f"/products/{id_a}", json={"sku": "COF-B"}, headers=h_admin())
    assert r.status_code == 409


# -------------------------- Filtres, tri, pagination --------------------------

def test_filters_sort_pagination(client: TestClient):
    # données
    client.post("/products", json=mk_product(sku="F1", name="Alpha",  price=4.0, quantity=5,  category="beans", brand="X"), headers=h_admin())
    client.post("/products", json=mk_product(sku="F2", name="beta",   price=9.0, quantity=15, category="beans", brand="Y"), headers=h_admin())
    client.post("/products", json=mk_product(sku="F3", name="Delta",  price=6.0, quantity=12, category="caps",  brand="X"), headers=h_admin())

    # filtre q insensible casse (beta)
    r = client.get("/products?q=BeTa", headers=h_read())
    assert r.status_code == 200
    names = [p["name"] for p in r.json()]
    assert names == ["beta"]

    # filtre category + brand + range prix
    r = client.get("/products?category=beans&brand=Y&min_price=5&max_price=10", headers=h_read())
    data = r.json()
    assert len(data) == 1 and data[0]["sku"] == "F2"

    # tri desc sur price
    r = client.get("/products?sort_by=price&sort_dir=desc", headers=h_read())
    prices = [p["price"] for p in r.json()]
    assert prices == sorted(prices, reverse=True)

    # pagination
    r = client.get("/products?sort_by=sku&sort_dir=asc&skip=1&limit=1", headers=h_read())
    assert len(r.json()) == 1

def test_invalid_sort_by_pattern(client: TestClient):
    r = client.get("/products?sort_by=__drop_table__", headers=h_read())
    assert r.status_code == 422  # pattern Query bloque


# -------------------------- Endpoints démo --------------------------

def test_adjust_stock_and_active_flags(client: TestClient, events_log: list):
    p = mk_product(sku="STK-1", quantity=2)
    pid = client.post("/products", json=p, headers=h_admin()).json()["id"]

    # +5 stock
    events_log.clear()
    r = client.patch(f"/products/{pid}/stock?delta=5", headers=h_admin())
    assert r.status_code == 200
    assert r.json()["quantity"] == 7
    assert any(e.get("event") == "product.updated" for e in events_log)

    # -10 stock -> 409 insuffisant
    r = client.patch(f"/products/{pid}/stock?delta=-10", headers=h_admin())
    assert r.status_code == 409

    # désactiver
    events_log.clear()
    r = client.patch(f"/products/{pid}/active?is_active=false", headers=h_admin())
    assert r.status_code == 200 and (r.json()["is_active"] is False)
    assert any(e.get("event") == "product.deactivated" for e in events_log)


# -------------------------- Conflit de version (optimistic locking) --------------------------

def test_optimistic_locking_conflict(test_engine, TestingSessionLocal, client: TestClient):
    """
    Simule deux sessions concurrentes :
    - S1 lit A (version 1)
    - S2 modifie/commit A (version 2)
    - S1 tente de modifier A avec version 1 -> 409
    """
    # Crée un produit
    base = mk_product(sku="LOCK-1", name="Lock")
    pid = client.post("/products", json=base, headers=h_admin()).json()["id"]

    # Ouvre deux sessions séparées
    from app.models.product import Product
    S1 = TestingSessionLocal()
    S2 = TestingSessionLocal()
    try:
        # S1 lit
        p1 = S1.get(Product, pid)
        assert p1 is not None
        old_version = p1.version

        # S2 modifie et commit (incrémente la version)
        p2 = S2.get(Product, pid)
        p2.price = 8.88
        S2.commit()
        S2.refresh(p2)
        assert p2.version == old_version + 1

        # S1 tente une maj avec ancien état (via API avec If-Match)
        r = client.put(
            f"/products/{pid}",
            json={"quantity": (p1.quantity or 0) + 1},
            headers={**h_admin(), "If-Match": str(old_version)},
        )
        assert r.status_code == 409, f"Expected 409, got {r.status_code}, body={r.text}"
    finally:
        S1.close()
        S2.close()

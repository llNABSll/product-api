import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.repositories import product_repository as repo
from app.schemas.product_schema import ProductCreate, ProductUpdate
from unittest.mock import MagicMock

# --- Helpers tri ---
def test_resolve_sort_known_and_unknown():
    asc_clause = repo._resolve_sort("id", "asc")
    desc_clause = repo._resolve_sort("name", "desc")
    fallback_clause = repo._resolve_sort("unknown", "asc")

    # SQLAlchemy génère "products.id ASC" etc. (nom de la table, pas de la classe)
    assert str(asc_clause) == "products.id ASC"
    assert str(desc_clause) == "products.name DESC"
    assert str(fallback_clause) == "products.id ASC"


# --- CRUD ---
def test_create_and_get_and_delete(session):
    data = ProductCreate(sku="S1", name="Test", price=1.0, quantity=1)
    p = repo.create_product(session, data)
    assert p.id is not None

    found = repo.get_product(session, p.id)
    assert found.sku == "S1"

    found_by_sku = repo.get_by_sku(session, "S1")
    assert found_by_sku.id == p.id

    deleted = repo.delete_product(session, p.id)
    assert deleted.id == p.id

    assert repo.get_product(session, p.id) is None


def test_create_product_integrityerror(monkeypatch, session):
    """
    Simule une contrainte d'unicité pendant create_product.
    Vérifie que rollback est bien appelé et que l'IntegrityError est propagée.
    """
    data = ProductCreate(sku="DUP", name="Dup", price=1.0, quantity=1)

    # Patch commit pour lever IntegrityError
    def bad_commit():
        raise IntegrityError("dup", {}, None)

    monkeypatch.setattr(session, "commit", bad_commit)
    monkeypatch.setattr(session, "rollback", MagicMock())

    with pytest.raises(IntegrityError):
        repo.create_product(session, data)
    session.rollback.assert_called_once()


def test_list_products_filters_and_sort(session):
    repo.create_product(
        session,
        ProductCreate(sku="S2", name="Apple", price=5.0, quantity=2, brand="B", category="C"),
    )
    repo.create_product(
        session,
        ProductCreate(sku="S3", name="Banana", price=10.0, quantity=3, brand="B", category="C"),
    )

    rows = repo.list_products(
        session,
        q="app", category="C", brand="B", min_price=1, max_price=10,
        only_active=True, sort_by="name", sort_dir="asc", skip=0, limit=10,
    )
    assert any(r.name == "Apple" for r in rows)


def test_update_product_ok(session):
    p = repo.create_product(session, ProductCreate(sku="S4", name="Old", price=1.0, quantity=1))
    updated = repo.update_product(session, p.id, ProductUpdate(name="New"))
    assert updated.name == "New"


def test_update_product_not_found(session):
    result = repo.update_product(session, 9999, ProductUpdate(name="X"))
    assert result is None


def test_update_product_integrityerror(monkeypatch, session):
    p = repo.create_product(session, ProductCreate(sku="S5", name="Tmp", price=1.0, quantity=1))

    def bad_commit():
        raise IntegrityError("fail", "params", "orig")

    monkeypatch.setattr(session, "commit", bad_commit)

    with pytest.raises(IntegrityError):
        repo.update_product(session, p.id, ProductUpdate(name="X"))


def test_update_product_staledataerror(monkeypatch, session):
    p = repo.create_product(session, ProductCreate(sku="S6", name="Tmp", price=1.0, quantity=1))

    def bad_commit():
        raise StaleDataError("stale")

    monkeypatch.setattr(session, "commit", bad_commit)

    with pytest.raises(StaleDataError):
        repo.update_product(session, p.id, ProductUpdate(name="X"))


def test_delete_product_not_found(session):
    assert repo.delete_product(session, 9999) is None

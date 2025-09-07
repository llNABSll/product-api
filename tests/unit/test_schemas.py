import pytest
import math
from datetime import datetime, timezone
from unittest.mock import MagicMock
from pydantic import ValidationError

from app.schemas.product_schema import (
    ProductBase, ProductCreate, ProductUpdate, ProductResponse
)


# ----- ProductBase / ProductCreate -----
def test_product_create_valid():
    p = ProductCreate(
        sku="SKU123",
        name="Produit",
        description="Desc",
        price=10.0,
        quantity=5,
        vat_rate=0.2,
        unit="kg",
        brand="Brand",
        category="Cat",
        is_active=True,
    )
    assert p.sku == "SKU123"
    assert p.vat_rate == 0.2


@pytest.mark.parametrize("field,value", [
    ("sku", ""),  # trop court
    ("name", ""),  # trop court
])
def test_product_create_string_constraints(field, value):
    data = dict(sku="SKU1", name="Produit", price=1.0, quantity=1)
    data[field] = value
    with pytest.raises(ValidationError):
        ProductCreate(**data)


@pytest.mark.parametrize("field,value", [
    ("price", -1.0),
    ("quantity", -5),
    ("vat_rate", -0.1),
    ("vat_rate", 1.1),
])
def test_product_create_numeric_constraints(field, value):
    data = dict(sku="SKU1", name="Produit", price=1.0, quantity=1)
    data[field] = value
    with pytest.raises(ValidationError):
        ProductCreate(**data)


@pytest.mark.parametrize("field,value", [
    ("price", math.nan),
    ("price", math.inf),
    ("price", -math.inf),
    ("vat_rate", math.nan),
])
def test_product_create_non_finite(field, value):
    data = dict(sku="SKU1", name="Produit", price=1.0, quantity=1)
    data[field] = value
    with pytest.raises(ValidationError):
        ProductCreate(**data)


@pytest.mark.parametrize("field,max_len", [
    ("description", 1000),
    ("unit", 32),
    ("brand", 128),
    ("category", 128),
])
def test_product_create_field_too_long(field, max_len):
    data = dict(sku="SKU1", name="Produit", price=1.0, quantity=1)
    data[field] = "x" * (max_len + 1)
    with pytest.raises(ValidationError):
        ProductCreate(**data)


# ----- ProductUpdate -----
def test_product_update_empty_ok():
    u = ProductUpdate()
    # 👉 exclude_unset / exclude_none pour ne pas garder les None
    assert u.model_dump(exclude_unset=True, exclude_none=True) == {}


def test_product_update_valid():
    u = ProductUpdate(price=5.0, quantity=10)
    assert u.price == 5.0
    assert u.quantity == 10


@pytest.mark.parametrize("field,value", [
    ("price", -1.0),
    ("vat_rate", 1.5),
    ("price", math.nan),
])
def test_product_update_invalid(field, value):
    with pytest.raises(ValidationError):
        ProductUpdate(**{field: value})


# ----- ProductResponse -----
def test_product_response_from_orm():
    fake_obj = MagicMock()
    fake_obj.id = 1
    fake_obj.version = 2
    fake_obj.created_at = datetime.now(timezone.utc)
    fake_obj.updated_at = datetime.now(timezone.utc)
    fake_obj.sku = "SKU"
    fake_obj.name = "Produit"
    fake_obj.description = "desc"
    fake_obj.price = 1.0
    fake_obj.quantity = 2
    fake_obj.vat_rate = 0.1
    fake_obj.unit = "kg"
    fake_obj.brand = "Brand"
    fake_obj.category = "Cat"
    fake_obj.is_active = True

    resp = ProductResponse.model_validate(fake_obj)
    assert resp.id == 1
    assert resp.version == 2
    assert resp.sku == "SKU"
    assert resp.is_active is True

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select, asc, desc, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.models.product import Product
from app.schemas.product_schema import ProductCreate, ProductUpdate

logger = logging.getLogger(__name__)

# --- Helpers tri ---

_SORT_MAP = {
    "id": Product.id,
    "sku": Product.sku,
    "name": Product.name,
    "price": Product.price,
    "quantity": Product.quantity,
    "created_at": Product.created_at,
    "updated_at": Product.updated_at,
}

def _resolve_sort(sort_by: str, sort_dir: str):
    """
    Retourne une clause ORDER BY sécurisée.
    - Si la colonne demandée n'est pas connue, on retombe sur id (évite l'injection).
    - Direction asc/desc normalisée.
    """
    col = _SORT_MAP.get(sort_by)
    if col is None:
        logger.debug("champ de tri inconnu; fallback sur id", extra={"sort_by": sort_by})
        col = Product.id
    return asc(col) if str(sort_dir).lower() == "asc" else desc(col)


# --- CRUD ---

def create_product(db: Session, data: ProductCreate) -> Product:
    """
    Création d’un produit.
    - Lève IntegrityError si SKU déjà utilisé.
    """
    db_product = Product(**data.model_dump())
    db.add(db_product)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.exception("conflit d'unicité lors de la création", extra={"sku": data.sku})
        raise
    db.refresh(db_product)
    logger.debug("produit créé", extra={"id": db_product.id, "sku": db_product.sku})
    return db_product


def get_product(db: Session, product_id: int) -> Optional[Product]:
    """Lecture par id (None si absent)."""
    p = db.get(Product, product_id)
    logger.debug("lecture produit par id", extra={"id": product_id, "found": bool(p)})
    return p


def get_by_sku(db: Session, sku: str) -> Optional[Product]:
    """Lecture par SKU (None si absent)."""
    stmt = select(Product).where(Product.sku == sku)
    p = db.execute(stmt).scalars().first()
    logger.debug("lecture produit par sku", extra={"sku": sku, "found": bool(p)})
    return p


def list_products(
    db: Session,
    *,
    q: str | None = None,
    category: str | None = None,
    brand: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    only_active: bool = True,
    sort_by: str = "id",
    sort_dir: str = "asc",
    skip: int = 0,
    limit: int = 10,
) -> list[Product]:
    """
    Liste paginée avec filtres :
    - q : recherche partielle sur name (lower like)
    - category / brand
    - min_price / max_price
    - only_active : True pour filtrer sur is_active
    - tri : sort_by in [id, sku, name, price, quantity, created_at, updated_at], asc|desc
    """
    stmt = select(Product)

    if q:
        stmt = stmt.where(func.lower(Product.name).like(f"%{q.lower()}%"))
    if category:
        stmt = stmt.where(Product.category == category)
    if brand:
        stmt = stmt.where(Product.brand == brand)
    if min_price is not None:
        stmt = stmt.where(Product.price >= min_price)
    if max_price is not None:
        stmt = stmt.where(Product.price <= max_price)
    if only_active:
        stmt = stmt.where(Product.is_active.is_(True))

    stmt = stmt.order_by(_resolve_sort(sort_by, sort_dir)).offset(skip).limit(limit)
    rows = list(db.execute(stmt).scalars().all())
    logger.debug(
        "liste produits",
        extra={
            "count": len(rows),
            "q": q,
            "category": category,
            "brand": brand,
            "min_price": min_price,
            "max_price": max_price,
            "only_active": only_active,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "skip": skip,
            "limit": limit,
        },
    )
    return rows


def update_product(db: Session, product_id: int, data: ProductUpdate) -> Optional[Product]:
    """
    Mise à jour partielle.
    - Lève IntegrityError (unicité SKU)
    - Lève StaleDataError (optimistic locking) si la version ne correspond plus
    - Retourne None si id inconnu
    """
    db_product = get_product(db, product_id)
    if not db_product:
        logger.debug("mise à jour: produit introuvable", extra={"id": product_id})
        return None

    # Applique uniquement les champs envoyés
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(db_product, key, value)

    try:
        db.commit()  # Si quelqu’un a modifié entre-temps, StaleDataError ici (via version_id_col)
    except IntegrityError:
        db.rollback()
        logger.exception("conflit d'unicité lors de la mise à jour", extra={"id": product_id})
        raise
    except StaleDataError:
        db.rollback()
        logger.exception("conflit de version (optimistic locking)", extra={"id": product_id})
        raise

    db.refresh(db_product)
    logger.debug("produit mis à jour", extra={"id": db_product.id, "version": db_product.version})
    return db_product


def delete_product(db: Session, product_id: int) -> Optional[Product]:
    """
    Suppression.
    Retourne l’objet supprimé (ou None si id inconnu).
    """
    db_product = get_product(db, product_id)
    if not db_product:
        logger.debug("suppression: produit introuvable", extra={"id": product_id})
        return None
    db.delete(db_product)
    db.commit()
    logger.debug("produit supprimé", extra={"id": product_id})
    return db_product

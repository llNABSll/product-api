from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status, Header
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.schemas.product_schema import ProductCreate, ProductUpdate, ProductResponse
from app.services.product_service import (
    ProductService,
    NotFoundError,
    SKUAlreadyExistsError,
    ConcurrencyConflictError,
    InsufficientStockError,
)
from app.security.security import require_read, require_write

router = APIRouter(prefix="/products", tags=["produits"])
logger = logging.getLogger(__name__)

# --- Dépendance DB ---
def get_db():
    """
    Ouvre une session SQLAlchemy par requête.
    La fermeture est gérée automatiquement dans le finally.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ===================== CRUD =====================

@router.post(
    "/",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_write)],
)
def create(product: ProductCreate, db: Session = Depends(get_db)):
    """
    Crée un produit.
    Sécurité : exige le rôle d'écriture (JWT Keycloak).
    """
    svc = ProductService(db)
    try:
        created = svc.create(product)
        logger.info("product created", extra={"id": created.id, "sku": created.sku})
        return created
    except SKUAlreadyExistsError:
        logger.debug("create conflict: sku already exists", extra={"sku": product.sku})
        raise HTTPException(status_code=409, detail="SKU déjà utilisé")


@router.get(
    "/",
    response_model=list[ProductResponse],
    dependencies=[Depends(require_read)],
)
def list_products(
    q: Optional[str] = Query(None, description="Recherche partielle sur le nom"),
    category: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    only_active: bool = Query(True),
    sort_by: Literal["id", "sku", "name", "price", "quantity", "created_at", "updated_at"] = Query("id"),
    sort_dir: Literal["asc", "desc"] = Query("asc"),
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    Liste paginée avec filtres/tri/pagination.
    Sécurité : rôle lecture requis.
    """
    svc = ProductService(db)
    rows = svc.list(
        q=q,
        category=category,
        brand=brand,
        min_price=min_price,
        max_price=max_price,
        only_active=only_active,
        sort_by=sort_by,
        sort_dir=sort_dir,
        skip=skip,
        limit=limit,
    )
    logger.debug(
        "products listed",
        extra={"count": len(rows), "q": q, "category": category, "brand": brand, "skip": skip, "limit": limit},
    )
    return rows


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    dependencies=[Depends(require_read)],
)
def read(product_id: int, db: Session = Depends(get_db)):
    """
    Détail d’un produit par id.
    Sécurité : rôle lecture requis.
    """
    svc = ProductService(db)
    try:
        p = svc.get(product_id)
        logger.debug("product read", extra={"id": product_id})
        return p
    except NotFoundError:
        logger.debug("product not found", extra={"id": product_id})
        raise HTTPException(status_code=404, detail="Produit non trouvé")


@router.put(
    "/{product_id}",
    response_model=ProductResponse,
    dependencies=[Depends(require_write)],
)
def update(
    product_id: int,
    product: ProductUpdate,
    db: Session = Depends(get_db),
    if_match: Optional[str] = Header(None, alias="If-Match"),
):
    """
    Mise à jour d’un produit.
    - Support du verrou optimiste via l’en-tête If-Match contenant la version attendue.
    Sécurité : rôle écriture requis.
    """
    svc = ProductService(db)
    try:
        expected_version = int(if_match) if if_match is not None else None
    except ValueError:
        logger.debug("invalid If-Match header", extra={"if_match": if_match})
        raise HTTPException(status_code=400, detail="If-Match doit être un entier")

    try:
        updated = svc.update(product_id, product, expected_version=expected_version)
        logger.info("product updated", extra={"id": product_id, "version": updated.version})
        return updated
    except NotFoundError:
        logger.debug("update failed: not found", extra={"id": product_id})
        raise HTTPException(status_code=404, detail="Produit non trouvé")
    except SKUAlreadyExistsError:
        logger.debug("update conflict: sku already exists", extra={"id": product_id})
        raise HTTPException(status_code=409, detail="SKU déjà utilisé")
    except ConcurrencyConflictError:
        logger.debug("update conflict: version mismatch", extra={"id": product_id, "If-Match": if_match})
        raise HTTPException(status_code=409, detail="Conflit de version : rechargez puis réessayez")


@router.delete(
    "/{product_id}",
    response_model=ProductResponse,
    dependencies=[Depends(require_write)],
)
def delete(product_id: int, db: Session = Depends(get_db)):
    """
    Suppression d’un produit.
    Sécurité : rôle écriture requis.
    """
    svc = ProductService(db)
    try:
        deleted = svc.delete(product_id)
        logger.info("product deleted", extra={"id": product_id})
        return deleted
    except NotFoundError:
        logger.debug("delete failed: not found", extra={"id": product_id})
        raise HTTPException(status_code=404, detail="Produit non trouvé")

# ===================== Extras utiles =====================

@router.get(
    "/sku/{sku}",
    response_model=ProductResponse,
    dependencies=[Depends(require_read)],
)
def read_by_sku(sku: str, db: Session = Depends(get_db)):
    """
    Récupération par SKU exact.
    Sécurité : rôle lecture requis.
    """
    svc = ProductService(db)
    product = svc.get_by_sku(sku)
    if not product:
        logger.debug("product not found by sku", extra={"sku": sku})
        raise HTTPException(status_code=404, detail="Produit non trouvé")
    logger.debug("product read by sku", extra={"sku": sku, "id": product.id})
    return product


@router.patch(
    "/{product_id}/stock",
    response_model=ProductResponse,
    dependencies=[Depends(require_write)],
)
def adjust_stock(
    product_id: int,
    delta: int = Query(..., description="Ex: +5 ou -3"),
    db: Session = Depends(get_db),
):
    """
    Ajuste le stock (ajout/retrait).
    Sécurité : rôle écriture requis.
    """
    svc = ProductService(db)
    try:
        updated = svc.adjust_stock(product_id, delta)
        logger.info("stock adjusted", extra={"id": product_id, "delta": delta, "new_qty": updated.quantity})
        return updated
    except NotFoundError:
        logger.debug("stock adjust failed: not found", extra={"id": product_id})
        raise HTTPException(status_code=404, detail="Produit non trouvé")
    except InsufficientStockError:
        logger.debug("stock adjust failed: insufficient", extra={"id": product_id, "delta": delta})
        raise HTTPException(status_code=409, detail="Stock insuffisant")


@router.patch(
    "/{product_id}/active",
    response_model=ProductResponse,
    dependencies=[Depends(require_write)],
)
def set_active(
    product_id: int,
    is_active: bool = Query(...),
    db: Session = Depends(get_db),
):
    """
    Active/Désactive le produit (flag logique).
    Sécurité : rôle écriture requis.
    """
    svc = ProductService(db)
    try:
        updated = svc.set_active(product_id, is_active)
        logger.info("product active flag changed", extra={"id": product_id, "is_active": is_active})
        return updated
    except NotFoundError:
        logger.debug("set_active failed: not found", extra={"id": product_id})
        raise HTTPException(status_code=404, detail="Produit non trouvé")

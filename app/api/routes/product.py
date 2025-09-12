# app/api/routes/product.py
from __future__ import annotations

import logging
from typing import List, Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status, Header
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.product_schema import ProductCreate, ProductUpdate, ProductResponse, StockAdjust, ActiveToggle
from app.services.product_service import (
    ProductService,
    NotFoundError,
    SKUAlreadyExistsError,
    ConcurrencyConflictError,
    InsufficientStockError,
)
from app.infra.events.rabbitmq import rabbitmq
from app.security.security import require_read, require_write

router = APIRouter(prefix="/products", tags=["Products"])
logger = logging.getLogger(__name__)

# ---------- Messages ----------
PRODUCT_NOT_FOUND_MSG = "Product not found"
SKU_ALREADY_EXISTS_MSG = "SKU already exists"
VERSION_CONFLICT_MSG = "Product has been modified elsewhere"
INSUFFICIENT_STOCK_MSG = "Insufficient stock"

# ---------- Dépendance ProductService ----------
def get_product_service(db: Session = Depends(get_db)) -> ProductService:
    return ProductService(db, rabbitmq)

# ===================== CRUD =====================

@router.post(
    "/",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_write)],
)
async def create_product(
    product: ProductCreate,
    svc: ProductService = Depends(get_product_service),
):
    """Crée un produit (sécurité : rôle écriture requis)."""
    try:
        created = await svc.create(product)
        logger.info("product created", extra={"id": created.id, "sku": created.sku})
        return created
    except SKUAlreadyExistsError:
        raise HTTPException(status_code=409, detail=SKU_ALREADY_EXISTS_MSG)


@router.get(
    "/",
    response_model=List[ProductResponse],
    dependencies=[Depends(require_read)],
)
def list_products(
    q: Optional[str] = Query(None, description="Recherche partielle sur nom/sku"),
    category: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    only_active: bool = Query(True),
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    sort_by: Literal["id", "name", "sku", "price", "created_at", "updated_at"] = Query("id"),
    sort_dir: Literal["asc", "desc"] = Query("asc"),
    svc: ProductService = Depends(get_product_service),
):
    """Liste paginée avec filtres/tri (sécurité : rôle lecture requis)."""
    rows = svc.list(
        q=q,
        category=category,
        brand=brand,
        min_price=min_price,
        max_price=max_price,
        only_active=only_active,
        skip=skip,
        limit=limit,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    logger.debug("products listed", extra={"count": len(rows)})
    return rows


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    dependencies=[Depends(require_read)],
)
def read_product(product_id: int, svc: ProductService = Depends(get_product_service)):
    """Détail d’un produit par ID (sécurité : rôle lecture requis)."""
    try:
        return svc.get(product_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail=PRODUCT_NOT_FOUND_MSG)


@router.put(
    "/{product_id}",
    response_model=ProductResponse,
    dependencies=[Depends(require_write)],
)
async def update_product(
    product_id: int,
    product: ProductUpdate,
    if_match: Optional[str] = Header(None, alias="If-Match"),
    svc: ProductService = Depends(get_product_service),
):
    """Met à jour un produit (optimistic locking via If-Match)."""
    try:
        expected_version = int(if_match) if if_match is not None else None
    except ValueError:
        raise HTTPException(status_code=400, detail="If-Match doit être un entier")

    try:
        updated = await svc.update(product_id, product, expected_version=expected_version)
        logger.info("product updated", extra={"id": product_id, "version": updated.version})
        return updated
    except NotFoundError:
        raise HTTPException(status_code=404, detail=PRODUCT_NOT_FOUND_MSG)
    except SKUAlreadyExistsError:
        raise HTTPException(status_code=409, detail=SKU_ALREADY_EXISTS_MSG)
    except ConcurrencyConflictError:
        raise HTTPException(status_code=409, detail=VERSION_CONFLICT_MSG)

@router.delete(
    "/{product_id}",
    response_model=ProductResponse,
    dependencies=[Depends(require_write)],
)
async def delete_product(product_id: int, svc: ProductService = Depends(get_product_service)):
    """Supprime un produit (sécurité : rôle écriture requis)."""
    try:
        deleted = await svc.delete(product_id)
        logger.info("product deleted", extra={"id": product_id})
        return deleted
    except NotFoundError:
        raise HTTPException(status_code=404, detail=PRODUCT_NOT_FOUND_MSG)

# ===================== Extras =====================

@router.patch(
    "/{product_id}/stock",
    response_model=ProductResponse,
    dependencies=[Depends(require_write)],
)
async def adjust_stock(
    product_id: int,
    body: StockAdjust,
    svc: ProductService = Depends(get_product_service),
):
    """Ajuste le stock d’un produit (peut lever une erreur si stock insuffisant)."""
    try:
        return await svc.adjust_stock(product_id, body.delta)
    except NotFoundError:
        raise HTTPException(status_code=404, detail=PRODUCT_NOT_FOUND_MSG)
    except InsufficientStockError:
        raise HTTPException(status_code=409, detail=INSUFFICIENT_STOCK_MSG)


@router.patch(
    "/{product_id}/active",
    response_model=ProductResponse,
    dependencies=[Depends(require_write)],
)
async def set_active(
    product_id: int,
    body: ActiveToggle, 
    svc: ProductService = Depends(get_product_service),
):
    """Active/désactive un produit."""
    try:
        return await svc.set_active(product_id, body.is_active)
    except NotFoundError:
        raise HTTPException(status_code=404, detail=PRODUCT_NOT_FOUND_MSG)
@router.get(
    "/sku/{sku}",
    response_model=ProductResponse,
    dependencies=[Depends(require_read)],
)
async def get_by_sku(
    sku: str,
    svc: ProductService = Depends(get_product_service),
):
    """Cherche un produit par SKU."""
    prod = svc.get_by_sku(sku)
    if not prod:
        raise HTTPException(status_code=404, detail=PRODUCT_NOT_FOUND_MSG)
    return prod

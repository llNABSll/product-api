from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status, Header
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.product_schema import ProductCreate, ProductUpdate, ProductResponse
from app.services.product_service import (
    ProductService,
    NotFoundError,
    SKUAlreadyExistsError,
    ConcurrencyConflictError,
    InsufficientStockError,
)
from app.security.security import require_read, require_write
from app.infra.events.rabbitmq import rabbitmq

router = APIRouter(prefix="/products", tags=["produits"])
logger = logging.getLogger(__name__)


# --- Dépendance pour ProductService ---
def get_product_service(db: Session = Depends(get_db)) -> ProductService:
    """
    Fournit un ProductService par requête avec :
      - une session DB (scopée à la requête),
      - une instance RabbitMQ globale et réutilisée.
    """
    return ProductService(db, rabbitmq)


# ===================== CRUD =====================

@router.post(
    "/",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_write)],
)
async def create(
    product: ProductCreate,
    svc: ProductService = Depends(get_product_service),
):
    """Crée un produit (sécurité : rôle écriture requis)."""
    try:
        created = await svc.create(product)
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
        sort_by=sort_by,
        sort_dir=sort_dir,
        skip=skip,
        limit=limit,
    )
    logger.debug("products listed", extra={"count": len(rows)})
    return rows


@router.get(
    "/{product_id}",
    response_model=ProductResponse,
    dependencies=[Depends(require_read)],
)
def read(product_id: int, svc: ProductService = Depends(get_product_service)):
    """Détail d’un produit par ID (sécurité : rôle lecture requis)."""
    try:
        return svc.get(product_id)
    except NotFoundError:
        logger.debug("product not found", extra={"id": product_id})
        raise HTTPException(status_code=404, detail="Produit non trouvé")


@router.put(
    "/{product_id}",
    response_model=ProductResponse,
    dependencies=[Depends(require_write)],
)
async def update(
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
        raise HTTPException(status_code=404, detail="Produit non trouvé")
    except SKUAlreadyExistsError:
        raise HTTPException(status_code=409, detail="SKU déjà utilisé")
    except ConcurrencyConflictError:
        raise HTTPException(status_code=409, detail="Conflit de version : rechargez puis réessayez")


@router.delete(
    "/{product_id}",
    response_model=ProductResponse,
    dependencies=[Depends(require_write)],
)
async def delete(product_id: int, svc: ProductService = Depends(get_product_service)):
    """Supprime un produit (sécurité : rôle écriture requis)."""
    try:
        return await svc.delete(product_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Produit non trouvé")


# ===================== Extras =====================

@router.get(
    "/sku/{sku}",
    response_model=ProductResponse,
    dependencies=[Depends(require_read)],
)
def read_by_sku(sku: str, svc: ProductService = Depends(get_product_service)):
    """Récupère un produit par SKU exact (sécurité : rôle lecture requis)."""
    product = svc.get_by_sku(sku)
    if not product:
        raise HTTPException(status_code=404, detail="Produit non trouvé")
    return product


@router.patch(
    "/{product_id}/stock",
    response_model=ProductResponse,
    dependencies=[Depends(require_write)],
)
async def adjust_stock(
    product_id: int,
    delta: int = Query(..., description="Ex: +5 ou -3"),
    svc: ProductService = Depends(get_product_service),
):
    """Ajuste le stock (sécurité : rôle écriture requis)."""
    try:
        return await svc.adjust_stock(product_id, delta)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Produit non trouvé")
    except InsufficientStockError:
        raise HTTPException(status_code=409, detail="Stock insuffisant")


@router.patch(
    "/{product_id}/active",
    response_model=ProductResponse,
    dependencies=[Depends(require_write)],
)
async def set_active(
    product_id: int,
    is_active: bool = Query(...),
    svc: ProductService = Depends(get_product_service),
):
    """Active ou désactive le produit (sécurité : rôle écriture requis)."""
    try:
        return await svc.set_active(product_id, is_active)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Produit non trouvé")

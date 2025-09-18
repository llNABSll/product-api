from __future__ import annotations

import logging
from typing import Optional, List

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.models.product_models import Product
from app.schemas.product_schema import ProductCreate, ProductUpdate
from app.repositories import product_repository as repo
from app.infra.events.contracts import MessagePublisher 

logger = logging.getLogger(__name__)

# ---------- Messages constants ----------
PRODUCT_NOT_FOUND_MSG = "Produit non trouvé"
SKU_ALREADY_EXISTS_MSG = "SKU déjà utilisé"
INSUFFICIENT_STOCK_MSG = "Stock insuffisant"
VERSION_CONFLICT_MSG = "Le produit a été modifié entre-temps"

# ---------- Exceptions domaine ----------
class NotFoundError(Exception):
    pass


class SKUAlreadyExistsError(Exception):
    pass


class ConcurrencyConflictError(Exception):
    """Optimistic locking: l'entité a été modifiée ailleurs pendant la MAJ."""


class InsufficientStockError(Exception):
    pass


# ---------- Service ----------
class ProductService:
    """
    Couche métier pour Product.
    - Orchestration repository + règles de gestion + publication d'événements.
    - Convertit les exceptions bas niveau (IntegrityError, StaleDataError)
      en exceptions domaine plus parlantes pour la couche API.
    """

    def __init__(self, db: Session, mq: MessagePublisher):
        self.db = db
        self.mq = mq 

    # ----- READ -----
    def get(self, product_id: int) -> Product:
        p = repo.get_product(self.db, product_id)
        if not p:
            logger.debug("produit introuvable", extra={"id": product_id})
            raise NotFoundError(PRODUCT_NOT_FOUND_MSG)
        logger.debug("produit lu", extra={"id": p.id, "sku": p.sku})
        return p

    def get_by_sku(self, sku: str) -> Optional[Product]:
        p = repo.get_by_sku(self.db, sku)
        logger.debug("lecture par sku", extra={"sku": sku, "found": bool(p)})
        return p

    def list(
        self,
        *,
        q: Optional[str] = None,
        category: Optional[str] = None,
        brand: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        only_active: bool = True,
        sort_by: str = "id",
        sort_dir: str = "asc",
        skip: int = 0,
        limit: int = 10,
    ) -> List[Product]:
        rows = repo.list_products(
            self.db,
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
            "liste produits (service)",
            extra={
                "count": len(rows),
                "q": q,
                "category": category,
                "brand": brand,
                "skip": skip,
                "limit": limit,
            },
        )
        return rows

    # ----- WRITE -----
    async def create(self, data: ProductCreate) -> Product:
        if repo.get_by_sku(self.db, data.sku):
            logger.debug("création refusée: SKU déjà utilisé", extra={"sku": data.sku})
            raise SKUAlreadyExistsError(SKU_ALREADY_EXISTS_MSG)
        try:
            product = repo.create_product(self.db, data)
        except IntegrityError:
            logger.debug("création refusée (integrity error)", extra={"sku": data.sku})
            raise SKUAlreadyExistsError(SKU_ALREADY_EXISTS_MSG)

        await self.mq.publish_message(
            "product.created", {"id": product.id, "sku": product.sku, "name": product.name, "price": product.price}
        )
        logger.info("produit créé", extra={"id": product.id, "sku": product.sku})
        return product

    async def update(
        self,
        product_id: int,
        data: ProductUpdate,
        *,
        expected_version: Optional[int] = None,
    ) -> Product:
        current = repo.get_product(self.db, product_id)
        if not current:
            logger.debug("mise à jour: produit introuvable", extra={"id": product_id})
            raise NotFoundError(PRODUCT_NOT_FOUND_MSG)

        if expected_version is not None and current.version != expected_version:
            logger.debug(
                "conflit de version (pré-check)",
                extra={
                    "id": product_id,
                    "expected": expected_version,
                    "actual": current.version,
                },
            )
            raise ConcurrencyConflictError(VERSION_CONFLICT_MSG)

        try:
            product = repo.update_product(self.db, product_id, data)
        except IntegrityError:
            logger.debug("mise à jour refusée (integrity error)", extra={"id": product_id})
            raise SKUAlreadyExistsError(SKU_ALREADY_EXISTS_MSG)
        except StaleDataError:
            logger.debug("conflit de version (stale data)", extra={"id": product_id})
            raise ConcurrencyConflictError(VERSION_CONFLICT_MSG)

        await self.mq.publish_message(
            "product.updated", {"id": product.id, "sku": product.sku, "name": product.name, "price": product.price}
        )
        logger.info(
            "produit mis à jour",
            extra={"id": product.id, "sku": product.sku, "version": product.version},
        )
        return product

    async def delete(self, product_id: int) -> Product:
        product = repo.delete_product(self.db, product_id)
        if not product:
            logger.debug("suppression: produit introuvable", extra={"id": product_id})
            raise NotFoundError(PRODUCT_NOT_FOUND_MSG)

        await self.mq.publish_message("product.deleted", {"id": product.id, "sku": product.sku})
        logger.info("produit supprimé", extra={"id": product.id, "sku": product.sku})
        return product

    # ----- Règles métier -----
    async def adjust_stock(self, product_id: int, delta: int) -> Product:
        product = self.get(product_id)
        new_qty = (product.quantity or 0) + int(delta)
        if new_qty < 0:
            logger.debug(
                "ajustement stock refusé (stock insuffisant)",
                extra={"id": product_id, "qty": product.quantity, "delta": delta},
            )
            raise InsufficientStockError(INSUFFICIENT_STOCK_MSG)

        logger.debug(
            "ajustement stock",
            extra={"id": product_id, "old_qty": product.quantity, "delta": delta},
        )
        return await self.update(product_id, ProductUpdate(quantity=new_qty))

    async def set_active(self, product_id: int, is_active: bool) -> Product:
        product = await self.update(product_id, ProductUpdate(is_active=is_active))
        event = "product.activated" if is_active else "product.deactivated"
        await self.mq.publish_message(event, {"id": product.id, "sku": product.sku})
        logger.info(
            "changement d'état actif",
            extra={"id": product.id, "sku": product.sku, "is_active": is_active},
        )
        return product

    async def upsert_by_sku(self, data: ProductCreate) -> Product:
        existing = repo.get_by_sku(self.db, data.sku)
        if not existing:
            logger.debug("upsert: création (SKU inexistant)", extra={"sku": data.sku})
            return await self.create(data)

        logger.debug("upsert: mise à jour (SKU existant)", extra={"id": existing.id, "sku": existing.sku})
        patch = ProductUpdate(**data.model_dump())
        return await self.update(existing.id, patch)

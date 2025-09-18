# app/infra/events/handlers.py  (PRODUCT-API)

from __future__ import annotations

import logging
from typing import Dict, List

from sqlalchemy.orm import Session

from app.services.product_service import ProductService, InsufficientStockError

logger = logging.getLogger(__name__)


def _get_service(db: Session) -> ProductService:
    """
    Instancie un ProductService câblé avec RabbitMQ.
    """
    from app.infra.events.rabbitmq import rabbitmq
    return ProductService(db, mq=rabbitmq)


# --------------------------
# Utilitaires internes
# --------------------------

def _clean_items(payload: dict) -> List[Dict[str, int]]:
    """
    Extrait une liste d'items {product_id, quantity} propre et typée.
    """
    raw = payload.get("items", [])
    items: List[Dict[str, int]] = []
    if not isinstance(raw, list):
        return items
    for it in raw:
        try:
            pid = int(it["product_id"])
            qty = int(it["quantity"])
            if qty < 0:
                logger.warning("[handlers] quantité négative ignorée: %s", it)
                continue
            items.append({"product_id": pid, "quantity": qty})
        except Exception:
            logger.warning("[handlers] item invalide ignoré: %s", it)
    return items


def _clean_deltas(payload: dict) -> List[Dict[str, int]]:
    raw = payload.get("deltas", [])
    deltas: List[Dict[str, int]] = []
    if not isinstance(raw, list):
        return deltas
    for it in raw:
        try:
            pid = int(it["product_id"])
            d = int(it["delta"])
            if d == 0:
                continue
            deltas.append({"product_id": pid, "delta": d})
        except Exception:
            logger.warning("[handlers] delta invalide ignoré: %s", it)
    return deltas


# --------------------------
# Handlers stock (inchangés)
# --------------------------

async def handle_order_created(payload: dict, db: Session):
    svc = _get_service(db)
    order_id = payload.get("id")
    items = _clean_items(payload)

    if not items:
        logger.info("[order.created] commande %s sans items -> no-op", order_id)
        return

    try:
        for it in items:
            product = svc.get(it["product_id"])
            available = product.quantity or 0
            if available < it["quantity"]:
                raise InsufficientStockError(
                    f"Produit {it['product_id']} insuffisant (demande {it['quantity']}, dispo {available})"
                )

        for it in items:
            await svc.adjust_stock(it["product_id"], -it["quantity"])

        db.commit()
        logger.info("[order.created] stock réservé pour commande %s", order_id)
    except InsufficientStockError as e:
        db.rollback()
        logger.warning("[order.created] rollback %s -> %s", order_id, e)
        await svc.mq.publish_message("order.rejected", {
            "id": order_id,
            "reason": str(e),
            "items": items,
        })


async def handle_order_items_delta(payload: dict, db: Session):
    svc = _get_service(db)
    order_id = payload.get("id")
    deltas = _clean_deltas(payload)

    if not deltas:
        logger.info("[order.items_delta] %s sans delta -> no-op", order_id)
        return

    try:
        for d in deltas:
            if d["delta"] > 0:
                product = svc.get(d["product_id"])
                if (product.quantity or 0) < d["delta"]:
                    raise InsufficientStockError(
                        f"Stock insuffisant produit {d['product_id']}"
                    )

        for d in deltas:
            await svc.adjust_stock(d["product_id"], -d["delta"])

        db.commit()
        logger.info("[order.items_delta] %s: deltas appliqués", order_id)
    except InsufficientStockError as e:
        db.rollback()
        logger.warning("[order.items_delta] rollback %s -> %s", order_id, e)
        await svc.mq.publish_message("order.rejected", {
            "id": order_id,
            "reason": str(e),
            "deltas": deltas,
        })


async def handle_order_cancelled(payload: dict, db: Session):
    svc = _get_service(db)
    order_id = payload.get("id")
    items = _clean_items(payload)
    if not items:
        return
    for it in items:
        await svc.adjust_stock(it["product_id"], +it["quantity"])
    db.commit()
    logger.info("[order.cancelled] %s: stock réinjecté", order_id)


async def handle_order_rejected(payload: dict, db: Session):
    logger.info("[order.rejected] %s -> no stock action", payload.get("id"))


async def handle_order_deleted(payload: dict, db: Session):
    svc = _get_service(db)
    order_id = payload.get("id")
    status = (payload.get("status") or "").lower()
    items = _clean_items(payload)
    if not items:
        return
    if status != "rejected":
        for it in items:
            await svc.adjust_stock(it["product_id"], +it["quantity"])
        db.commit()
        logger.info("[order.deleted] %s: stock réinjecté", order_id)


async def handle_order_updated(payload: dict, db: Session):
    logger.info("[order.updated] %s status=%s", payload.get("id"), payload.get("status"))


# --------------------------
# Nouveau handler : calcul du prix
# --------------------------

async def handle_order_price_request(payload: dict, db: Session):
    svc = _get_service(db)

    customer_id = payload.get("customer_id")
    items = _clean_items(payload)
    if not customer_id or not items:
        logger.warning("[order.request_price] payload invalide: %s", payload)
        return

    enriched = []
    total = 0.0
    for it in items:
        prod = svc.get(it["product_id"])
        price = float(prod.price or 0)
        qty = it["quantity"]
        enriched.append({
            "product_id": it["product_id"],
            "quantity": qty,
            "unit_price": price
        })
        total += price * qty

    await svc.mq.publish_message(
        "order.price_calculated",
        {"customer_id": customer_id, "items": enriched, "total": total},
    )
    logger.info("[order.request_price] prix envoyés pour client %s", customer_id)
    
async def handle_order_price_calculated(payload: dict, db: Session):
    """
    Décrémente le stock après calcul du prix.
    """
    svc = _get_service(db)
    items = _clean_items(payload)

    if not items:
        logger.info("[order.price_calculated] aucun item -> no-op")
        return

    try:
        for it in items:
            await svc.adjust_stock(it["product_id"], -it["quantity"])
        db.commit()
        logger.info("[order.price_calculated] stock décrémenté")
    except Exception:
        db.rollback()
        logger.exception("[order.price_calculated] échec décrémentation")

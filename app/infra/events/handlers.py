# app/infra/events/handlers.py (PRODUCT-API)

from __future__ import annotations

import logging
from typing import Dict, List

from sqlalchemy.orm import Session
from app.services.product_service import ProductService, InsufficientStockError

logger = logging.getLogger(__name__)


# --------------------------
# Helpers
# --------------------------

def _get_service(db: Session) -> ProductService:
    """Instancie un ProductService câblé avec RabbitMQ."""
    from app.infra.events.rabbitmq import rabbitmq
    return ProductService(db, mq=rabbitmq)


def _clean_items(payload: dict) -> List[Dict[str, int]]:
    """Extrait une liste d'items {product_id, quantity} propre et typée."""
    raw = payload.get("items", [])
    items: List[Dict[str, int]] = []
    if not isinstance(raw, list):
        return items
    for it in raw:
        try:
            pid = int(it["product_id"])
            qty = int(it["quantity"])
            if qty <= 0:
                logger.warning("[handlers] quantité invalide ignorée: %s", it)
                continue
            items.append({"product_id": pid, "quantity": qty})
        except Exception:
            logger.warning("[handlers] item invalide ignoré: %s", it)
    return items


def _clean_deltas(payload: dict) -> List[Dict[str, int]]:
    """Extrait une liste de deltas {product_id, delta}."""
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
# Handlers stock
# --------------------------

async def handle_order_items_delta(payload: dict, db: Session):
    """Met à jour le stock quand des items changent (ajout/suppression)."""
    svc = _get_service(db)
    order_id = payload.get("order_id")
    deltas = _clean_deltas(payload)

    if not deltas:
        logger.info("[order.items_delta] %s sans delta -> no-op", order_id)
        return

    try:
        # Vérifie la dispo si on ajoute du stock consommé
        for d in deltas:
            if d["delta"] > 0:
                product = svc.get(d["product_id"])
                if (product.quantity or 0) < d["delta"]:
                    raise InsufficientStockError(
                        f"Stock insuffisant produit {d['product_id']}"
                    )

        # Applique les deltas
        for d in deltas:
            await svc.adjust_stock(d["product_id"], -d["delta"])

        db.commit()
        logger.info("[order.items_delta] %s: deltas appliqués", order_id)

    except InsufficientStockError as e:
        db.rollback()
        logger.warning("[order.items_delta] rollback %s -> %s", order_id, e)
        await svc.mq.publish_message("order.rejected", {
            "order_id": order_id,
            "reason": str(e),
            "deltas": deltas,
        })


async def handle_order_cancelled(payload: dict, db: Session):
    """Réinjecte le stock en cas d'annulation de commande."""
    svc = _get_service(db)
    order_id = payload.get("order_id")
    items = _clean_items(payload)
    if not items:
        return
    for it in items:
        await svc.adjust_stock(it["product_id"], +it["quantity"])
    db.commit()
    logger.info("[order.cancelled] %s: stock réinjecté", order_id)


async def handle_order_rejected(payload: dict, db: Session):
    """Pas d’action stock si rejet (le stock n’a jamais été réservé)."""
    logger.info("[order.rejected] %s -> no stock action", payload.get("order_id"))


async def handle_order_deleted(payload: dict, db: Session):
    """Réinjecte le stock si une commande supprimée avait réservé du stock."""
    svc = _get_service(db)
    order_id = payload.get("order_id")
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
    """Log les mises à jour de commande."""
    logger.info("[order.updated] %s status=%s", payload.get("order_id"), payload.get("status"))


# --------------------------
# Price flow
# --------------------------

async def handle_order_price_request(payload: dict, db: Session):
    """
    Calcule le prix total et renvoie un événement `order.price_calculated`.
    (Ne touche pas au stock ici !)
    """
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
        {
            "order_id": payload.get("order_id"),
            "customer_id": customer_id,
            "items": enriched,
            "total": total,
        },
    )
    logger.info("[order.request_price] prix envoyés pour client %s", customer_id)


# --------------------------
# Stock reservation (après validation client)
# --------------------------

async def handle_order_ready_for_stock(payload: dict, db: Session):
    """
    Après validation du client, on réserve le stock.
    """
    svc = _get_service(db)
    order_id = payload.get("order_id")
    items = _clean_items(payload)

    if not order_id or not items:
        logger.warning("[order.customer_validated] payload invalide")
        return

    try:
        # Vérifie le stock dispo
        for it in items:
            product = svc.get(it["product_id"])
            available = product.quantity or 0
            if available < it["quantity"]:
                raise InsufficientStockError(
                    f"Produit {it['product_id']} insuffisant "
                    f"(demande {it['quantity']}, dispo {available})"
                )

        # Réserve le stock
        for it in items:
            await svc.adjust_stock(it["product_id"], -it["quantity"])

        db.commit()
        logger.info("[order.customer_validated] stock décrémenté, commande confirmée")
        await svc.mq.publish_message("order.confirmed", {
            "order_id": order_id,
            "items": items,
        })

    except InsufficientStockError as e:
        db.rollback()
        logger.warning("[order.customer_validated] rollback %s -> %s", order_id, e)
        await svc.mq.publish_message("order.rejected", {
            "order_id": order_id,
            "reason": str(e),
            "items": items,
        })

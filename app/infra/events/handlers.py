# app/infra/events/handlers.py

import logging
from sqlalchemy.orm import Session

from app.services.product_service import ProductService, InsufficientStockError

logger = logging.getLogger(__name__)


def _get_service(db: Session) -> ProductService:
    """Factory pour avoir toujours un ProductService avec RabbitMQ branché."""
    from app.infra.events.rabbitmq import rabbitmq
    return ProductService(db, mq=rabbitmq)


# ----- ORDER CREATED -----
async def handle_order_created(payload: dict, db: Session):
    """
    Décrémente le stock pour chaque produit de la commande.
    Transaction atomique : si un produit est insuffisant -> rollback complet.
    """
    svc = _get_service(db)
    items = payload.get("items", [])

    try:
        # 1. Vérification globale
        for item in items:
            pid, qty = item["product_id"], item["quantity"]
            product = svc.get(pid)
            if (product.quantity or 0) < qty:
                raise InsufficientStockError(
                    f"Produit {pid} insuffisant (demande {qty}, dispo {product.quantity})"
                )

        # 2. Décrément atomique
        for item in items:
            await svc.adjust_stock(item["product_id"], -item["quantity"])

        db.commit()
        logger.info(f"[order.created] Commande {payload['id']} stock décrémenté")

    except InsufficientStockError as e:
        db.rollback()
        logger.warning(f"[order.created] rollback commande {payload['id']} -> {e}")

        # 👉 Publier un event "order.rejected" vers RabbitMQ
        await svc.mq.publish_message(
            "order.rejected",
            {
                "id": payload["id"],
                "reason": str(e),
                "items": items,
            },
        )
        return


# ----- ORDER DELETED -----
async def handle_order_deleted(payload: dict, db: Session):
    """
    Réinjecte le stock si une commande est supprimée.
    payload attendu :
    {
        "id": 123,
        "customer_id": 1,
        "items": [{"product_id": 3, "quantity": 2}, ...]
    }
    """
    svc = _get_service(db)

    for item in payload.get("items", []):
        pid = item["product_id"]
        qty = item["quantity"]
        await svc.adjust_stock(pid, qty)
        logger.info(f"[order.deleted] Stock réinjecté produit {pid} (+{qty})")


# ----- ORDER UPDATED -----
async def handle_order_updated(payload: dict, db: Session):
    """
    Ajuste le stock selon le statut ou les modifications.
    payload attendu :
    {
        "id": 123,
        "status": "cancelled",
        "items": [{"product_id": 3, "quantity": 2}, ...]
    }
    """
    svc = _get_service(db)

    status = payload.get("status")
    if status == "cancelled":
        for item in payload.get("items", []):
            pid = item["product_id"]
            qty = item["quantity"]
            await svc.adjust_stock(pid, qty)
            logger.info(f"[order.updated] Annulation → stock réinjecté produit {pid} (+{qty})")
    else:
        logger.info(f"[order.updated] Pas d’ajustement pour statut {status}")

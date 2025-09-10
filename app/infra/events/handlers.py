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
        try:
            await svc.adjust_stock(pid, -qty)
            logger.info(f"[order.created] Stock décrémenté produit {pid} (-{qty})")
        except InsufficientStockError:
            logger.warning(
                f"[order.created] Stock insuffisant produit {pid} (commande {payload['id']})"
            )


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

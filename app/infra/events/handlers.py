# app/infra/events/handlers.py

from __future__ import annotations

import logging
from typing import Dict, List

from sqlalchemy.orm import Session

from app.services.product_service import ProductService, InsufficientStockError

logger = logging.getLogger(__name__)


def _get_service(db: Session) -> ProductService:
    """
    Factory qui retourne un ProductService câblé avec RabbitMQ.
    On injecte ici le singleton rabbitmq pour permettre la publication d'événements.
    """
    from app.infra.events.rabbitmq import rabbitmq
    return ProductService(db, mq=rabbitmq)


# --------------------------
# Utilitaires internes
# --------------------------

def _clean_items(payload: dict) -> List[Dict[str, int]]:
    """
    Extrait une liste d'items {product_id, quantity} propre et typée.
    Ignore les entrées mal formées.
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
                # Si jamais on reçoit une qty négative par erreur
                logger.warning("[handlers] quantité négative ignorée: %s", it)
                continue
            items.append({"product_id": pid, "quantity": qty})
        except Exception:
            logger.warning("[handlers] item invalide ignoré: %s", it)
    return items


def _clean_deltas(payload: dict) -> List[Dict[str, int]]:
    """
    Extrait une liste de deltas {product_id, delta} propre et typée.
    """
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
# Handlers d'événements
# --------------------------

async def handle_order_created(payload: dict, db: Session):
    """
    Réservation initiale : décrémente le stock pour chaque item de la commande.
    Transaction atomique : si un item est insuffisant -> rollback complet + publish order.rejected.
    payload attendu:
    {
        "id": 123,
        "status": "pending",
        "items": [{"product_id": 3, "quantity": 2}, ...]
    }
    """
    svc = _get_service(db)
    order_id = payload.get("id")
    items = _clean_items(payload)

    if not items:
        logger.info("[order.created] commande %s sans items -> no-op", order_id)
        return

    try:
        # 1) Vérifier toutes les disponibilités avant d'ajuster
        for it in items:
            product = svc.get(it["product_id"])  # méthode sync côté service
            available = (product.quantity or 0)
            if available < it["quantity"]:
                raise InsufficientStockError(
                    f"Produit {it['product_id']} insuffisant (demande {it['quantity']}, dispo {available})"
                )

        # 2) Appliquer tous les ajustements (réservation = décrémentation)
        for it in items:
            await svc.adjust_stock(it["product_id"], -it["quantity"])

        db.commit()
        logger.info("[order.created] commande %s: stock réservé pour %d items", order_id, len(items))

    except InsufficientStockError as e:
        db.rollback()
        logger.warning("[order.created] rollback commande %s -> %s", order_id, e)
        # On notifie l'échec de réservation
        await svc.mq.publish_message("order.rejected", {
            "id": order_id,
            "reason": str(e),
            "items": items,
        })


async def handle_order_items_delta(payload: dict, db: Session):
    """
    Ajustements fins après modification des items.
    Règle:
      - delta > 0 : on réserve plus  -> décrémenter le stock de 'delta'
      - delta < 0 : on libère       -> incrémenter le stock de 'abs(delta)'
    Transaction atomique : si un delta ne passe pas (stock insuffisant), tout est rollback.
    payload attendu:
    {
        "id": 123,
        "deltas": [{"product_id": 7, "delta": +3}, {"product_id": 8, "delta": -1}],
        "updated_at": "..."
    }
    """
    svc = _get_service(db)
    order_id = payload.get("id")
    deltas = _clean_deltas(payload)

    if not deltas:
        logger.info("[order.items_delta] commande %s sans delta -> no-op", order_id)
        return

    try:
        # 1) Vérifier toutes les réservations additionnelles avant d'appliquer quoi que ce soit
        for d in deltas:
            if d["delta"] > 0:
                product = svc.get(d["product_id"])
                needed = d["delta"]
                available = (product.quantity or 0)
                if available < needed:
                    raise InsufficientStockError(
                        f"Delta insuffisant produit {d['product_id']} (besoin {needed}, dispo {available})"
                    )

        # 2) Appliquer les deltas (dans l'ordre fourni)
        for d in deltas:
            # -delta si on réserve plus ; +abs(delta) si on libère
            await svc.adjust_stock(d["product_id"], -d["delta"])

        db.commit()
        logger.info("[order.items_delta] commande %s: deltas appliqués %s", order_id, deltas)

    except InsufficientStockError as e:
        db.rollback()
        logger.warning("[order.items_delta] rollback commande %s -> %s", order_id, e)
        await svc.mq.publish_message("order.rejected", {
            "id": order_id,
            "reason": str(e),
            "deltas": deltas,
        })


async def handle_order_cancelled(payload: dict, db: Session):
    """
    Annulation de commande : réinjecter *toute* la réservation.
    payload attendu:
    {
        "id": 123,
        "items": [{"product_id": 3, "quantity": 2}, ...]
    }
    """
    svc = _get_service(db)
    order_id = payload.get("id")
    items = _clean_items(payload)

    if not items:
        logger.info("[order.cancelled] commande %s sans items -> no-op", order_id)
        return

    for it in items:
        await svc.adjust_stock(it["product_id"], +it["quantity"])

    db.commit()
    logger.info("[order.cancelled] commande %s: stock réinjecté pour %d items", order_id, len(items))


async def handle_order_rejected(payload: dict, db: Session):
    """
    Commande rejetée : ne rien réinjecter.
    Le stock n’a pas été réservé en amont (rollback déjà fait dans handle_order_created).
    """
    order_id = payload.get("id")
    logger.info("[order.rejected] commande %s rejetée -> aucun ajustement stock", order_id)


async def handle_order_deleted(payload: dict, db: Session):
    """
    Suppression de commande :
    - si statut = rejected -> no-op (aucune réservation n'avait eu lieu)
    - sinon -> réinjecter le stock (comme cancelled).
    """
    svc = _get_service(db)
    order_id = payload.get("id")
    status = (payload.get("status") or "").lower()
    items = _clean_items(payload)

    if not items:
        logger.info("[order.deleted] commande %s sans items -> no-op", order_id)
        return

    if status == "rejected":
        logger.info("[order.deleted] commande %s supprimée (déjà rejetée) -> aucun ajustement stock", order_id)
        return

    for it in items:
        await svc.adjust_stock(it["product_id"], +it["quantity"])

    db.commit()
    logger.info("[order.deleted] commande %s: stock réinjecté pour %d items", order_id, len(items))


async def handle_order_updated(payload: dict, db: Session):
    """
    Mise à jour générique d'une commande.
    Ne modifie pas le stock (réservé aux events spécifiques).
    """
    order_id = payload.get("id")
    status = payload.get("status")
    logger.info("[order.updated] commande %s status=%s -> no-op stock", order_id, status)

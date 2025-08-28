from __future__ import annotations

import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import settings

logger = logging.getLogger(__name__)

# --- Engine SQLAlchemy ---
engine = create_engine(
    settings.DATABASE_URL,
    future=True,
    pool_pre_ping=True, # détecte et évite les connexions "zombies".
    echo=getattr(settings, "DB_ECHO", False),
)

# --- Session factory ---
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False, # contrôle explicite des flush (meilleure prévisibilité).
    autocommit=False, # on attend un commit explicite dans les services.
    future=True,
)

# --- Base déclarative ---
Base = declarative_base()


# --- Dépendance FastAPI pour obtenir une session ---
# Ouvre une session par requête HTTP, rollback sur exception, puis fermeture.
def get_db():
    db = SessionLocal()
    logger.debug("db session opened")
    try:
        yield db
    except Exception:
        # En cas d'exception durant le traitement, on annule la transaction en cours.
        try:
            db.rollback()
            logger.exception("db session rolled back due to exception")
        except Exception:
            # On loggue mais on ne masque pas l'erreur d'origine.
            logger.exception("db rollback failed")
        raise
    finally:
        try:
            db.close()
            logger.debug("db session closed")
        except Exception:
            logger.exception("db session close failed")

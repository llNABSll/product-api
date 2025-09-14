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
    pool_pre_ping=True,               # évite les connexions mortes
    echo=getattr(settings, "DB_ECHO", False),
)

# --- Session factory ---
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,                  # flush explicite
    autocommit=False,                 # commit explicite dans les services
    future=True,
)

# --- Base déclarative ---
Base = declarative_base()


def init_db() -> None:
    """
    Enregistre tous les modèles et crée les tables manquantes.
    IMPORTANT: il faut que les modules de modèles soient importés
    avant d'appeler Base.metadata.create_all().
    """
    # Importe les modèles (via le package aggregator)
    Base.metadata.create_all(bind=engine)
    logger.info("DB init: tables ensured")


# --- Dépendance FastAPI pour obtenir une session ---
def get_db():
    """
    Ouvre une session par requête HTTP.
    - rollback sur exception
    - fermeture systématique
    (Le commit reste à la charge du service si tu veux des transactions explicites.)
    """
    db = SessionLocal()
    logger.debug("db session opened")
    try:
        yield db
    except Exception:
        try:
            db.rollback()
            logger.exception("db session rolled back due to exception")
        except Exception:
            logger.exception("db rollback failed")
        raise
    finally:
        try:
            db.close()
            logger.debug("db session closed")
        except Exception:
            logger.exception("db session close failed")

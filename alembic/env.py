# alembic/env.py
from __future__ import annotations
import os
from logging.config import fileConfig
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL
from alembic import context
from app.core.database import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# --- Récupère DATABASE_URL ou fabrique-la proprement depuis POSTGRES_* ---
raw_url = os.environ.get("DATABASE_URL", "").strip()

def build_url_from_parts():
    return URL.create(
        "postgresql+psycopg2",
        username=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "change_me"),
        host=os.getenv("POSTGRES_SERVER", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "product"),
    )

if raw_url:
    DATABASE_URL = raw_url  # peut être une str ou on garde telle quelle
else:
    DATABASE_URL = build_url_from_parts()

# Petit diagnostic utile si ça rebugge :
try:
    s = str(DATABASE_URL)
    s.encode("utf-8")  # si ça pète ici, y'a un caractère chelou
except Exception as e:
    raise RuntimeError(f"Bad DATABASE_URL encoding: {DATABASE_URL!r} -> {e}")

def run_migrations_offline() -> None:
    context.configure(
        url=str(DATABASE_URL),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    connectable = create_engine(
        DATABASE_URL,  # accepte objet URL ou str
        poolclass=pool.NullPool,
        future=True,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

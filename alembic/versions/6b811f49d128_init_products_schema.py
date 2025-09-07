"""init products schema

Revision ID: 0001_init_products
Revises:
Create Date: 2025-08-29 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# Révisions
revision = "6b811f49d128"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("price", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("brand", sa.String(length=128), nullable=True),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("vat_rate", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("weight_gram", sa.Integer(), nullable=True),
        sa.Column("volume_ml", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.CheckConstraint("price >= 0", name="chk_product_price_nonneg"),
        sa.CheckConstraint("quantity >= 0", name="chk_product_quantity_nonneg"),
        sa.CheckConstraint("vat_rate >= 0 AND vat_rate <= 1", name="chk_product_vat_range"),
        sa.UniqueConstraint("sku", name="uq_product_sku"),
    )

    # Index “classiques” (sur les colonnes)
    op.create_index("ix_products_sku", "products", ["sku"], unique=False)
    op.create_index("ix_products_name", "products", ["name"], unique=False)

    # Index fonctionnel (case-insensitive) : lower(name)
    # Par sécurité, on crée explicitement l’Index fonctionnel pour PostgreSQL.
    op.execute('CREATE INDEX IF NOT EXISTS ix_products_name_ci ON products (lower(name));')


def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS ix_products_name_ci;')
    op.drop_index("ix_products_name", table_name="products")
    op.drop_index("ix_products_sku", table_name="products")
    op.drop_table("products")

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    String, Integer, Float, Boolean, DateTime,
    CheckConstraint, UniqueConstraint, Index, func, text
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.ext.hybrid import hybrid_property

from app.core.database import Base


class Product(Base):
    """
    Modèle produit.
    - Contraintes d'intégrité (prix/quantité/vat non négatifs, plage VAT 0..1).
    - Unicité business sur le SKU.
    - Index case-insensitive sur le nom (pour la recherche partielle).
    - Verrou optimiste via colonne 'version' (incrément à chaque update validé).
    """
    __tablename__ = "products"

    # --- Contraintes & index globaux ---
    __table_args__ = (
        # Sécurité des données
        CheckConstraint("price >= 0", name="chk_product_price_nonneg"),
        CheckConstraint("quantity >= 0", name="chk_product_quantity_nonneg"),
        CheckConstraint("vat_rate >= 0 AND vat_rate <= 1", name="chk_product_vat_range"),
        # Unicité business
        UniqueConstraint("sku", name="uq_product_sku"),
        # Index pour recherche insensible à la casse sur name
        Index("ix_products_name_ci", text("lower(name)")),
    )

    # --- Colonnes principales ---
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(String(1000))
    price: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    # --- Données complémentaires ---
    unit: Mapped[Optional[str]] = mapped_column(String(32))           # ex: "bag", "box", "bottle", "kg"
    brand: Mapped[Optional[str]] = mapped_column(String(128))
    category: Mapped[Optional[str]] = mapped_column(String(128))
    vat_rate: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))  # 0..1
    weight_gram: Mapped[Optional[int]] = mapped_column(Integer)       # grammes
    volume_ml: Mapped[Optional[int]] = mapped_column(Integer)         # millilitres
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # --- Timestamps ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # --- Verrou optimiste ---
    # version_id_col + version_id_generator:
    # SQLAlchemy compare la version avant UPDATE; si 0 lignes affectées -> StaleDataError.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    __mapper_args__ = {
        "version_id_col": version,
        "version_id_generator": lambda v: (v or 0) + 1,  # +1 à chaque UPDATE validé
    }

    # --- Propriété calculée (non persistée) ---
    @hybrid_property
    def price_with_vat(self) -> float:
        """Prix TTC arrondi à 2 décimales (calculé à la volée)."""
        return round(self.price * (1 + self.vat_rate), 2)

    def __repr__(self) -> str:
        """Représentation concise utile en logs/debug."""
        return f"<Product id={self.id} sku={self.sku!r} name={self.name!r} price={self.price} qty={self.quantity}>"

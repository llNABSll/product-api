# app/schemas/product_schema.py
from __future__ import annotations

from datetime import datetime
from typing import Optional
import math

from pydantic import BaseModel, Field, field_validator, ConfigDict


class ProductBase(BaseModel):
    """
    Schéma commun aux opérations de lecture/écriture.
    - Contraintes de longueur sur les chaînes.
    - Contraintes numériques (prix/quantité/vat non négatifs, vat ≤ 1).
    - Validation additionnelle pour refuser les valeurs non finies (NaN, ±Infinity),
      afin d'éviter des incohérences et les problèmes de sérialisation JSON.
    """
    sku: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    price: float = Field(..., ge=0)
    quantity: int = Field(..., ge=0)
    vat_rate: float = Field(0, ge=0, le=1)
    unit: Optional[str] = Field(None, max_length=32)
    brand: Optional[str] = Field(None, max_length=128)
    category: Optional[str] = Field(None, max_length=128)
    is_active: bool = True

    @field_validator("price", "vat_rate", mode="after")
    @classmethod
    def _must_be_finite(cls, v):
        """
        Refuse les valeurs non finies (NaN, +Infinity, -Infinity).
        Pydantic peut convertir certains types (ex: string "Infinity" -> float('inf')),
        on verrouille donc explicitement.
        """
        if v is not None and isinstance(v, float) and not math.isfinite(v):
            raise ValueError("must be finite")
        return v


class ProductCreate(ProductBase):
    """Payload pour la création: identique à ProductBase."""
    pass


class ProductUpdate(BaseModel):
    """
    Schéma de mise à jour partielle (tous les champs optionnels).
    Seuls les champs fournis seront appliqués par la couche repository/service.
    """
    sku: Optional[str] = Field(None, min_length=1, max_length=64)
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    price: Optional[float] = Field(None, ge=0)
    quantity: Optional[int] = Field(None, ge=0)
    vat_rate: Optional[float] = Field(None, ge=0, le=1)
    unit: Optional[str] = Field(None, max_length=32)
    brand: Optional[str] = Field(None, max_length=128)
    category: Optional[str] = Field(None, max_length=128)
    is_active: Optional[bool] = None

    @field_validator("price", "vat_rate", mode="after")
    @classmethod
    def _must_be_finite(cls, v):
        """Même verrouillage que ProductBase pour les valeurs non finies."""
        if v is not None and isinstance(v, float) and not math.isfinite(v):
            raise ValueError("must be finite")
        return v


class ProductResponse(ProductBase):
    """
    Représentation renvoyée par l’API.
    - Hérite des champs communs.
    - Ajoute les métadonnées techniques (id, version, timestamps).
    """
    id: int
    version: int
    created_at: datetime
    updated_at: datetime

    # Pydantic v2: permet la construction depuis des objets ORM (SQLAlchemy)
    model_config = ConfigDict(from_attributes=True)

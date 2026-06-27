"""
Modelo de producto
Implementación del modelo de datos
El atributo 'status' (Nuevo vs Regular) activa la lógica de balanceo de visibilidad.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import uuid


class ProductStatus(Enum):
    NEW = "new" # Producto nuevo -> activa balanceo de visibilidad
    REGULAR = "regular"  # Producto establecido

@dataclass
class Product:
    """
    Producto del catálogo UniMarket.
    El status NEW/REGULAR dispara la lógica de Búsqueda Tabú y Pareto.
    """
    name: str
    category: str
    price: float
    stock: int
    status: ProductStatus
    entrepreneur_id: str
    product_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    description: Optional[str] = None

    # Métricas para ranking Pareto 
    relevance_score: float = 0.0 # Score de relevancia para el usuario
    load_latency_ms: float = 0.0 # Latencia de carga del producto
    visibility_score: float = 0.0 # Score combinado de visibilidad

    def __repr__(self) -> str:
        return (
            f"Product({self.name!r}, {self.status.value}, "
            f"${self.price:.2f}, stock={self.stock})"
        )

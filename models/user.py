"""
Perfil de Usuario
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List
import uuid


class UserRole(Enum):
    BUYER        = "buyer"
    ENTREPRENEUR = "entrepreneur"


@dataclass
class UserProfile:
    """
    Perfil de usuario con preferencias e historial de transacciones.
    Roles: Comprador / Emprendedor.
    """
    role:  UserRole
    name:  str
    email: str

    user_id:              str             = field(default_factory=lambda: str(uuid.uuid4()))
    category_preferences: Dict[str, float] = field(default_factory=dict)
    transaction_history:  List[str]        = field(default_factory=list)

    # Umbral de seguridad de batería del dispositivo móvil
    battery_threshold_pct: float = 20.0

    def add_preference(self, category: str, weight: float) -> None:
        """Actualiza la preferencia por categoría (0.0 – 1.0)."""
        self.category_preferences[category] = max(0.0, min(1.0, weight))

    def record_transaction(self, transaction_id: str) -> None:
        self.transaction_history.append(transaction_id)

    def __repr__(self) -> str:
        return f"User({self.name!r}, {self.role.value}, prefs={list(self.category_preferences)})"

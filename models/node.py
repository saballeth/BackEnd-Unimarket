"""
Recurso del nodo de la red
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import List
import uuid


class NodeType(Enum):
    EDGE  = "edge"
    CLOUD = "cloud"
    GNB   = "gnb"


@dataclass
class NetworkNode:
    """
    Nodo de procesamiento en la arquitectura 5G Fog-Cloud.

    Atributos según especificación:
      - cpu_ghz -> f_s (potencia de CPU en GHz)
      - ram_total_gbit ->M_s (capacidad de RAM total en Gbit)
      - ram_used_gbit -> RAM actualmente en uso
      - active_power_w -> Potencia activa (Watts)
    """
    node_type: NodeType
    cpu_ghz: float # f_s
    ram_total_gbit: float # M_s
    ram_used_gbit: float
    bandwidth_mhz: float
    active_power_w: float
    idle_power_w: float
    distance_m: float # Distancia al gNB (0 para Cloud -> usa fibra)

    node_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_queue: List[str] = field(default_factory=list)

    # Propiedades derivadas
    @property
    def ram_available_gbit(self) -> float:
        """RAM libre = M_s - RAM en uso"""
        return max(0.0, self.ram_total_gbit - self.ram_used_gbit)

    @property
    def queue_length(self) -> int:
        return len(self.task_queue)

    @property
    def utilization(self) -> float:
        """Fracción de RAM utilizada (0.0 – 1.0)"""
        return self.ram_used_gbit / self.ram_total_gbit if self.ram_total_gbit > 0 else 0.0

    # Gestión de recursos
    def allocate(self, task_id: str, ram_gbit: float) -> bool:
        """Asigna RAM a una tarea. Retorna False si no hay recursos disponibles"""
        if self.ram_available_gbit < ram_gbit:
            return False
        self.ram_used_gbit += ram_gbit
        self.task_queue.append(task_id)
        return True

    def release(self, task_id: str, ram_gbit: float) -> None:
        """Libera RAM al completar una tarea """
        self.ram_used_gbit = max(0.0, self.ram_used_gbit - ram_gbit)
        if task_id in self.task_queue:
            self.task_queue.remove(task_id)

    def __repr__(self) -> str:
        return (
            f"Node({self.node_type.value.upper()}, id={self.node_id[:8]}, "
            f"RAM={self.ram_available_gbit:.1f}/{self.ram_total_gbit}Gb, "
            f"queue={self.queue_length})"
        )

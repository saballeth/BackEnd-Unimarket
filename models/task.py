"""
Tarea computacional
Implementación del modelo de datos
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import uuid
import time


class TaskStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    DISCARDED = "discarded" # Deadline violation o SINR insuficiente
    QUEUED = "queued" # En espera de recursos RAM

class OffloadingDecision(Enum):
    LOCAL = "local" # Procesamiento en el UE
    EDGE = "edge" # Offloading al servidor Edge
    CLOUD = "cloud" # Offloading al servidor Cloud

@dataclass
class Task:
    """
    Tarea computacional generada por el Equipo de Usuario (UE).

    Especificaciones:
      - I_m -> tamaño de entrada en bits
      - O_m -> tamaño de salida en bits
      - P_m -> potencia de transmisión uplink del UE
      - D_m -> plazo máximo de finalización en ms
    """
    user_id: str
    input_size_bits: float # I_m
    output_size_bits: float # O_m
    uplink_power_dbm: float # P_m 
    deadline_ms: float # D_m

    # Campos auto-generados
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)
    status: TaskStatus = TaskStatus.PENDING

    # Decisión de offloading (asignada por MOTOA)
    offloading_decision: Optional[OffloadingDecision] = None
    assigned_server_id: Optional[str] = None

    # Métricas resultantes del cálculo de costes
    tau_total_ms: Optional[float] = None # τ_total
    tau_uplink_ms: Optional[float] = None # τ_uplink
    tau_downlink_ms: Optional[float] = None # τ_downlink
    tau_comp_ms: Optional[float] = None # τ_comp
    tau_queue_ms: Optional[float] = None # τ_queue
    tau_ram_wait_ms: Optional[float] = None # τ_ram_wait
    energy_total_j: Optional[float] = None # E_total (Joules)

    @property
    def priority(self) -> float:
        """Prioridad = Potencia de transmisión uplink del UE"""
        return self.uplink_power_dbm

    def __repr__(self) -> str:
        return (
            f"Task(id={self.task_id[:8]}, user={self.user_id}, "
            f"I_m={self.input_size_bits:.0f}b, D_m={self.deadline_ms}ms, "
            f"status={self.status.value})"
        )

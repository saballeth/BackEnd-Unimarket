"""
Schemas Pydantic para la API REST de UniMarket
Backend API
"""
from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, validator

#  TAREAS 

class TaskOffloadRequest(BaseModel):
    user_id: str
    input_size_bits:float = Field(..., gt=0, description="I_m – tamaño de entrada (bits)")
    output_size_bits: float = Field(..., gt=0, description="O_m – tamaño de salida (bits)")
    uplink_power_dbm: float = Field(..., ge=-30, le=33, description="P_m – potencia UE (dBm)")
    deadline_ms: float = Field(..., gt=0, description="D_m – plazo (ms)")

class TaskOffloadResponse(BaseModel):
    task_id: str
    status: str
    offloading: Optional[str]
    server_id: Optional[str]
    tau_total_ms: Optional[float]
    energy_total_j: Optional[float]

class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    offloading: Optional[str]
    tau_uplink_ms: Optional[float]
    tau_downlink_ms: Optional[float]
    tau_comp_ms: Optional[float]
    tau_queue_ms: Optional[float]
    tau_ram_wait_ms: Optional[float]
    tau_total_ms: Optional[float]
    energy_total_j: Optional[float]

#  PRODUCTOS
class ProductStatusEnum(str, Enum):
    NEW = "new"
    REGULAR = "regular"

class ProductCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    category: str = Field(..., min_length=2)
    price: float = Field(..., gt=0)
    stock: int = Field(..., ge=0)
    status: ProductStatusEnum = ProductStatusEnum.NEW
    entrepreneur_id: str
    description: Optional[str] = None

class ProductResponse(BaseModel):
    product_id: str
    name: str
    category: str
    price: float
    stock: int
    status: str
    description: Optional[str]
    relevance_score: float = 0.0
class ProductSearchParams(BaseModel):
    q: str = ""
    category: Optional[str] = None
    max_results: int = Field(10, ge=1, le=50)
    tabu_ids: List[str] = Field(default_factory=list, description="IDs ya vistos (Búsqueda Tabú)")
#  PAGOS
class PaymentInitRequest(BaseModel):
    user_id: str
    product_id: str
    amount: float = Field(..., gt=0)
    payment_data: Dict[str, Any]
    uplink_power_dbm: float = Field(23.0, ge=-30, le=33)
    deadline_ms: float = Field(5000.0, gt=0)

class PaymentInitResponse(BaseModel):
    transaction_id: str
    status: str
    product_name: Optional[str]
    amount: float
    offloaded_to: Optional[str]
    server_id: Optional[str]
    audit_trail: List[dict] = []

class PaymentStatusResponse(BaseModel):
    transaction_id: str
    status: str
    offloaded_to: Optional[str]
    completed_at: Optional[float]
    error_message: Optional[str]
    audit_trail: List[dict]

#  USUARIOS
class UserRoleEnum(str, Enum):
    BUYER = "comprador"
    ENTREPRENEUR = "emprendedor"

class UserCreate(BaseModel):
    name: str
    email: str
    role: UserRoleEnum
    category_preferences: Dict[str, float] = Field(default_factory=dict)
    battery_threshold_pct: float = Field(20.0, ge=0, le=100)

class UserResponse(BaseModel):
    user_id: str
    name: str
    email: str
    role: str

#  INFRAESTRUCTURA y MONITOREO
class NodeStatus(BaseModel):
    node_id: str
    node_type: str
    ram_total_gbit: float
    ram_used_gbit: float
    ram_available_gbit:float
    utilization_pct: float
    queue_length: int
    cpu_ghz: float
    active_power_w: float

class InfrastructureStatus(BaseModel):
    nodes: List[NodeStatus]
    global_queue_length: int
    motoa_params: Dict[str, Any]

class OptimizeRequest(BaseModel):
    max_iterations: int = Field(100, ge=1, le=1000)
    alpha: float = Field(0.5, ge=0, le=1)
    beta: float = Field(0.5, ge=0, le=1)

class OptimizeResponse(BaseModel):
    converged: bool
    iterations: int
    initial_cost: float
    final_cost: float
    improvement_pct:float
    reassignments: int
    cost_history: List[float]

class JROFRequest(BaseModel):
    user_id: str
    query: str = ""
    category: Optional[str] = None
    max_results: int = Field(10, ge=1, le=50)
    deadline_ms: float = Field(5000.0, gt=0)
    snapshot_age_s: float = Field(0.0, ge=0)
    tabu_ids: List[str] = Field(default_factory=list)

class InfrastructureConfidenceResponse(BaseModel):
    snapshot_age_s: float
    utilization_mean: float
    queue_variance: float
    queue_pressure: float
    confidence: float

class JROFStateResponse(BaseModel):
    user_id: str
    query: str
    category: Optional[str]
    max_results: int
    product_count: int
    node_count: int
    infra_confidence: InfrastructureConfidenceResponse

class JROFResponse(BaseModel):
    policy_id: str
    converged: bool
    iterations: int
    state: JROFStateResponse
    selected_node_id: Optional[str]
    selected_node_type: Optional[str]
    selected_products: List[dict]
    policy: Dict[str, Any]
    feedback_trace: List[dict]

#  RECOMENDACIONES
class RecommendationResponse(BaseModel):
    user_id: str
    count: int
    products: List[dict]
    pareto_front_n: int

"""
Dependencias
Centraliza el estado compartido del sistema UniMarket:
  - Nodos de red (Edge + Cloud)
  - Motor MOTOA
  - Base de datos en memoria
  - Servicios
"""
from __future__ import annotations
from typing import Dict, List
from algorithms.motoa import MOTOAEngine
from config import (
    DEFAULT_ALPHA, DEFAULT_BETA,
    EDGE_CPU_GHZ, EDGE_RAM_GBIT, EDGE_BW_MHZ,
    CLOUD_CPU_GHZ, CLOUD_RAM_GBIT, CLOUD_BW_MHZ,
)
from models.node import NetworkNode, NodeType
from models.product import Product
from models.task import Task
from models.user import UserProfile
from persistence import (
    SQLiteEntityStore,
    payment_from_payload,
    payment_to_payload,
    product_from_payload,
    product_to_payload,
    task_from_payload,
    task_to_payload,
    user_from_payload,
    user_to_payload,
)
from services.notification import NotificationService
from services.jrof import JROFEngine
from services.payment_gateway import PaymentGateway
from services.recommendation import RecommendationService

# Nodos de red: 2 Edge + 1 Cloud
_NODES: List[NetworkNode] = [
    NetworkNode(
        node_type = NodeType.EDGE,
        cpu_ghz = EDGE_CPU_GHZ,
        ram_total_gbit = EDGE_RAM_GBIT,
        ram_used_gbit = 0.0,
        bandwidth_mhz = EDGE_BW_MHZ,
        active_power_w = 115.0,
        idle_power_w = 45.0,
        distance_m = 250.0,
    ),
    NetworkNode(
        node_type = NodeType.EDGE,
        cpu_ghz = EDGE_CPU_GHZ,
        ram_total_gbit = EDGE_RAM_GBIT,
        ram_used_gbit = 0.0,
        bandwidth_mhz = EDGE_BW_MHZ,
        active_power_w = 120.0,
        idle_power_w = 48.0,
        distance_m = 380.0,
    ),
    NetworkNode(
        node_type = NodeType.CLOUD,
        cpu_ghz = CLOUD_CPU_GHZ,
        ram_total_gbit = CLOUD_RAM_GBIT,
        ram_used_gbit = 0.0,
        bandwidth_mhz = CLOUD_BW_MHZ,
        active_power_w = 500.0,
        idle_power_w = 200.0,
        distance_m = 0.0,
    ),
]

# Motor MOTOA 
_MOTOA = MOTOAEngine(alpha=DEFAULT_ALPHA, beta=DEFAULT_BETA)

# Bases persistentes (SQLite local)
_TASKS_DB: SQLiteEntityStore[Task] = SQLiteEntityStore("tasks", task_to_payload, task_from_payload)
_PRODUCTS_DB: SQLiteEntityStore[Product] = SQLiteEntityStore("products", product_to_payload, product_from_payload)
_USERS_DB: SQLiteEntityStore[UserProfile] = SQLiteEntityStore("users", user_to_payload, user_from_payload)
_PAYMENTS_DB: SQLiteEntityStore = SQLiteEntityStore("payments", payment_to_payload, payment_from_payload)

# Servicios
_NOTIFIER = NotificationService()
_RECOMMENDER = RecommendationService()
_JROF= JROFEngine(recommender=_RECOMMENDER, motoa=_MOTOA)
_PAYMENT_GW = PaymentGateway(
    motoa_engine=_MOTOA,
    notification_svc=_NOTIFIER,
    products_store=_PRODUCTS_DB,
    users_store=_USERS_DB,
    payments_store=_PAYMENTS_DB,
)

#  Providers inyectables con FastAPI Depends

def get_nodes() -> List[NetworkNode]:  return _NODES
def get_motoa() -> MOTOAEngine:        return _MOTOA
def get_notifier() -> NotificationService:return _NOTIFIER
def get_recommender() -> RecommendationService: return _RECOMMENDER
def get_jrof() -> JROFEngine:        return _JROF
def get_payment_gw() -> PaymentGateway:     return _PAYMENT_GW
def get_tasks_db() -> SQLiteEntityStore[Task]:    return _TASKS_DB
def get_products_db() -> SQLiteEntityStore[Product]: return _PRODUCTS_DB
def get_users_db() -> SQLiteEntityStore[UserProfile]: return _USERS_DB

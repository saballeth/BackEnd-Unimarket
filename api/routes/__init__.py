"""Routers de la API de UniMarket."""

from .routes import infra_router, payments_router, products_router, reco_router, users_router
from .tasks import router as tasks_router

__all__ = [
    "infra_router",
    "payments_router",
    "products_router",
    "reco_router",
    "tasks_router",
    "users_router",
]

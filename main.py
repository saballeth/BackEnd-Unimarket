"""
UniMarket — Punto de entrada principal
Ecosistema 5G Fog-Cloud con motor MOTOA

Fases integradas:
  Fase 1: Infraestructura base (nodos Edge + Cloud, modelos de datos)
  Fase 2: Motor MOTOA (offloading multi-objetivo)
  Fase 3: Módulos integrados (Pagos, emprendedor, recomendaciones)
  Fase 4: Optimización por ajuste secuencial

Arranque:
  uvicorn main:app --reload --port 8000
"""
from __future__ import annotations
import logging
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes.tasks import router as tasks_router
from api.routes.routes import (
    products_router, payments_router,
    reco_router, users_router, infra_router, jrof_router,
)

#Logging 
logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("unimarket")


# Lifespan (startup/shutdown)
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicialización y limpieza del sistema."""
    logger.info("=" * 60)
    logger.info(" UniMarket Backend arrancando...")
    logger.info(" Arquitectura: 5G Fog-Cloud | Motor: MOTOA")
    logger.info(" Nodos Edge: 2  |  Nodos Cloud: 1")
    logger.info("=" * 60)
    yield

# FastAPI 
app = FastAPI(
    title = "UniMarket API",
    description = (
        "Backend del ecosistema UniMarket sobre arquitectura 5G Fog-Cloud. "
        "Motor de offloading MOTOA (Multi-Objective Task Offloading Algorithm) "
        "con optimización Pareto y ajuste secuencial."
    ),
    version     = "1.0.0",
    lifespan    = lifespan,
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

# CORS (para Flutter frontend)
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],   # En produccion hay que reemplazar con dominios reales
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)


#Middleware: Request timing
@app.middleware("http")
async def add_process_time(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed = (time.perf_counter() - t0) * 1000
    response.headers["X-Process-Time-Ms"] = f"{elapsed:.2f}"
    return response


# Exception handlers
@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


# Registro de routers 
API_V1 = "/api/v1"

app.include_router(tasks_router, prefix=API_V1)
app.include_router(products_router, prefix=API_V1)
app.include_router(payments_router, prefix=API_V1)
app.include_router(reco_router, prefix=API_V1)
app.include_router(users_router,prefix=API_V1)
app.include_router(infra_router,prefix=API_V1)
app.include_router(jrof_router, prefix=API_V1)

# Health Check
@app.get("/health", tags=["Sistema"])
def health_check():
    from api.deps import get_nodes, get_motoa
    nodes = get_nodes()
    motoa = get_motoa()
    return {
        "status": "healthy",
        "version": "1.0.0",
        "nodes_online": len(nodes),
        "motoa_params": motoa.params,
        "timestamp": time.time(),
    }


@app.get("/", tags=["Sistema"])
def root():
    return {
        "service": "UniMarket API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
        "endpoints": {
            "tasks": f"{API_V1}/tasks",
            "products": f"{API_V1}/products",
            "payments": f"{API_V1}/payments",
            "recommendations": f"{API_V1}/recommendations",
            "users": f"{API_V1}/users",
            "infrastructure": f"{API_V1}/infrastructure",
        },
    }

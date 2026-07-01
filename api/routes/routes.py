"""
Rutas API: Productos, pagos, recomendaciones e infraestructura
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from api.schemas import (
    InfrastructureStatus, NodeStatus, OptimizeRequest, OptimizeResponse,
    JROFRequest, JROFResponse, InfrastructureConfidenceResponse, JROFStateResponse,
    PaymentInitRequest, PaymentInitResponse, PaymentStatusResponse,
    ProductCreate, ProductResponse, ProductSearchParams, RecommendationResponse,
    UserCreate, UserResponse,
)
from api.deps import (
    get_motoa, get_nodes, get_tasks_db, get_products_db,
    get_users_db, get_payment_gw, get_recommender, get_notifier, get_jrof,
)
from models.product import Product, ProductStatus
from models.user import UserProfile, UserRole
from algorithms.sequential_tuning import SequentialTuner
from models.task import TaskStatus

#  PRODUCTOS
products_router = APIRouter(prefix="/products", tags=["Productos"])


@products_router.post("/", response_model=ProductResponse, status_code=201,
                      summary="Crea un nuevo producto (Módulo Emprendedor)")
def create_product(
    payload:     ProductCreate,
    products_db = Depends(get_products_db),
):
    product = Product(
        name = payload.name,
        category = payload.category,
        price = payload.price,
        stock = payload.stock,
        status = ProductStatus(payload.status.value),
        entrepreneur_id = payload.entrepreneur_id,
        description = payload.description,
    )
    products_db[product.product_id] = product
    return ProductResponse(
        product_id = product.product_id,
        name = product.name,
        category = product.category,
        price = product.price,
        stock = product.stock,
        status = product.status.value,
        description = product.description,
    )

@products_router.get("/search", summary="Búsqueda Tabú con ranking Pareto")
def search_products(
    q: str  = "",
    category: str = None,
    max_results: int = 10,
    user_id: str = None,
    products_db = Depends(get_products_db),
    users_db = Depends(get_users_db),
    recommender = Depends(get_recommender),
):
    """
    Motor de búsqueda Tabú integrado con recomendaciones Pareto
    - Filtra por query textual y categoría
    - Aplica balanceo Nuevo/Regular para visibilidad
    - Excluye productos sin stock
    """
    user = users_db.get(user_id) if user_id else None
    results = recommender.search(
        products = list(products_db.values()),
        query = q,
        category = category,
        user = user,
        max_results = max_results,
    )
    return {
        "count": len(results),
        "products": [r.to_dict() for r in results],
    }
@products_router.get("/{product_id}", response_model=ProductResponse)
def get_product(product_id: str, products_db = Depends(get_products_db)):
    p = products_db.get(product_id)
    if not p:
        raise HTTPException(404, "Product not found")
    return ProductResponse(
        product_id=p.product_id, name=p.name, category=p.category,
        price=p.price, stock=p.stock, status=p.status.value, description=p.description,
    )

@products_router.patch("/{product_id}/stock")
def update_stock(
    product_id: str,
    delta: int, # +N para reabastecer, -N para reservar
    products_db = Depends(get_products_db),
    notifier = Depends(get_notifier),
):
    p = products_db.get(product_id)
    if not p:
        raise HTTPException(404, "Producto no encontrado")
    new_stock = p.stock + delta
    if new_stock < 0:
        raise HTTPException(409, "Stock insuficiente")
    p.stock = new_stock
    products_db[p.product_id] = p
    if p.stock <= 3:
        from services.notification import NotificationType
        notifier.notify_low_stock(p.entrepreneur_id, p.name, p.stock)
    return {"product_id": product_id, "stock": p.stock}


#  PAGOS
payments_router = APIRouter(prefix="/payments", tags=["Pagos"])


@payments_router.post("/initiate", response_model=PaymentInitResponse, status_code=202,
                      summary="Inicia el flujo de pago con verificación MOTOA")
def initiate_payment(
    payload: PaymentInitRequest,
    products_db = Depends(get_products_db),
    users_db = Depends(get_users_db),
    nodes = Depends(get_nodes),
    motoa = Depends(get_motoa),
    gw = Depends(get_payment_gw),
):
    product = products_db.get(payload.product_id)
    if not product:
        raise HTTPException(404, "Product not found")
    buyer = users_db.get(payload.user_id)
    if not buyer:
        raise HTTPException(404, "User not found")
    from services.payment_gateway import PaymentRequest as GwRequest
    request = GwRequest(
        user_id = payload.user_id,
        product_id = payload.product_id,
        amount = payload.amount,
        payment_data = payload.payment_data,
        uplink_power_dbm = payload.uplink_power_dbm,
        deadline_ms = payload.deadline_ms,
    )
    global_queue = sum(n.queue_length for n in nodes)
    record = gw.process(request, product, buyer, nodes, global_queue)

    if record.status.value == "failed":
        raise HTTPException(
            status_code = 503,
            detail = {
                "error": "payment_failed",
                "reason": record.error_message,
                "txn_id": record.transaction_id,
                "trail": record.audit_trail,
            },
        )

    return PaymentInitResponse(
        transaction_id = record.transaction_id,
        status = record.status.value,
        product_name = product.name,
        amount = record.amount,
        offloaded_to = record.offloaded_to,
        server_id = record.server_id,
        audit_trail = record.audit_trail,
    )

@payments_router.get("/{txn_id}", response_model=PaymentStatusResponse,
                     summary="Consulta el estado de una transacción")
def get_payment(txn_id: str, gw = Depends(get_payment_gw)):
    record = gw.get_record(txn_id)
    if not record:
        raise HTTPException(404, "Transaction not found")
    return PaymentStatusResponse(
        transaction_id = record.transaction_id,
        status = record.status.value,
        offloaded_to = record.offloaded_to,
        completed_at = record.completed_at,
        error_message = record.error_message,
        audit_trail = record.audit_trail,
    )

#  RECOMENDACIONES
reco_router = APIRouter(prefix="/recommendations", tags=["Recomendaciones"])
@reco_router.get("/{user_id}", response_model=RecommendationResponse,
                 summary="Recomendaciones personalizadas con Pareto + visibilidad")
def get_recommendations(
    user_id: str,
    max_results: int = 10,
    users_db = Depends(get_users_db),
    products_db = Depends(get_products_db),
    recommender = Depends(get_recommender),
):
    user = users_db.get(user_id)
    results = recommender.get_recommendations(
        user = user,
        products = list(products_db.values()),
        max_results = max_results,
    )
    pareto_front_n = sum(1 for r in results if r.is_pareto_front)
    return RecommendationResponse(
        user_id = user_id,
        count = len(results),
        products = [r.to_dict() for r in results],
        pareto_front_n = pareto_front_n,
    )

#  USUARIOS
users_router = APIRouter(prefix="/users", tags=["Usuarios"])
@users_router.post("/", response_model=UserResponse, status_code=201)
def create_user(payload: UserCreate, users_db = Depends(get_users_db)):
    user = UserProfile(
        name = payload.name,
        email = payload.email,
        role = UserRole(payload.role.value),
        category_preferences = payload.category_preferences,
        battery_threshold_pct= payload.battery_threshold_pct,
    )
    users_db[user.user_id] = user
    return UserResponse(user_id=user.user_id, name=user.name,
                        email=user.email, role=user.role.value)

@users_router.get("/{user_id}/notifications")
def get_notifications(
    user_id: str,
    unread: bool = False,
    notifier = Depends(get_notifier),
):
    notifs = notifier.get_inbox(user_id, unread_only=unread)
    return {"user_id": user_id, "count": len(notifs),
            "notifications": [
                {"id": n.notif_id, "type": n.notif_type.value,
                 "title": n.title, "body": n.body, "ts": n.created_at}
                for n in notifs
            ]}

#  INFRAESTRUCTURA Y OPTIMIZACIÓN
infra_router = APIRouter(prefix="/infrastructure", tags=["Infraestructura"])
@infra_router.get("/status", response_model=InfrastructureStatus,
                  summary="Estado en tiempo real de los nodos Edge y Cloud")
def infra_status(
    nodes = Depends(get_nodes),
    motoa = Depends(get_motoa),
):
    return InfrastructureStatus(
        nodes = [
            NodeStatus(
                node_id = n.node_id,
                node_type = n.node_type.value,
                ram_total_gbit = n.ram_total_gbit,
                ram_used_gbit = n.ram_used_gbit,
                ram_available_gbit = n.ram_available_gbit,
                utilization_pct = round(n.utilization * 100, 2),
                queue_length = n.queue_length,
                cpu_ghz = n.cpu_ghz,
                active_power_w = n.active_power_w,
            )
            for n in nodes
        ],
        global_queue_length = sum(n.queue_length for n in nodes),
        motoa_params = motoa.params,
    )

@infra_router.post("/optimize", response_model=OptimizeResponse,
                   summary="Fase 4: Ajuste Secuencial (Sequential Tuning)")
def run_sequential_tuning(
    payload:  OptimizeRequest,
    tasks_db = Depends(get_tasks_db),
    nodes = Depends(get_nodes),
    motoa = Depends(get_motoa),
):
    """
    Ejecuta el bucle iterativo de Sequential Tuning
    Revisa secuencialmente cada tarea activa y reasigna si reduce el coste total sin violar restricciones.
    """
    active = [t for t in tasks_db.values() if t.status == TaskStatus.PROCESSING]
    if not active:
        raise HTTPException(404, "No hay tareas activas para optimizar")

    # Actualizar pesos si se pasan explícitamente
    motoa.alpha = payload.alpha
    motoa.beta = payload.beta

    tuner  = SequentialTuner(motoa, max_iterations=payload.max_iterations)
    result = tuner.optimize(active, nodes)

    return OptimizeResponse(
        converged = result.converged,
        iterations = result.iterations,
        initial_cost = result.initial_cost,
        final_cost = result.final_cost,
        improvement_pct = round(result.improvement_pct, 2),
        reassignments = result.reassignments,
        cost_history = result.cost_history,
    )

#  JROF — Joint Recommendation-Offloading Framework
jrof_router = APIRouter(prefix="/jrof", tags=["JROF"])

@jrof_router.post("/optimize", response_model=JROFResponse,
                  summary="Optimización alternante conjunta entre recomendación y offloading")
def optimize_joint_policy(
    payload: JROFRequest,
    users_db = Depends(get_users_db),
    products_db= Depends(get_products_db),
    nodes = Depends(get_nodes),
    jrof = Depends(get_jrof),
):
    user = users_db.get(payload.user_id)
    result = jrof.optimize(
        user=user,
        products=list(products_db.values()),
        nodes=nodes,
        query=payload.query,
        category=payload.category,
        max_results=payload.max_results,
        deadline_ms=payload.deadline_ms,
        snapshot_age_s=payload.snapshot_age_s,
        tabu_ids=payload.tabu_ids,
    )
    return JROFResponse(
        policy_id=result.policy_id,
        converged=result.converged,
        iterations=result.iterations,
        state=JROFStateResponse(
            user_id=result.state.user_id,
            query=result.state.query,
            category=result.state.category,
            max_results=result.state.max_results,
            product_count=result.state.product_count,
            node_count=result.state.node_count,
            infra_confidence=InfrastructureConfidenceResponse(
                snapshot_age_s=result.state.infra_confidence.snapshot_age_s,
                utilization_mean=result.state.infra_confidence.utilization_mean,
                queue_variance=result.state.infra_confidence.queue_variance,
                queue_pressure=result.state.infra_confidence.queue_pressure,
                confidence=result.state.infra_confidence.confidence,
            ),
        ),
        selected_node_id=result.selected_node_id,
        selected_node_type=result.selected_node_type,
        selected_products=result.selected_products,
        policy=result.policy,
        feedback_trace=result.feedback_trace,
    )

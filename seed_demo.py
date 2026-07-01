"""
Script de Seed y Demo — UniMarket
Pobla el sistema con datos de prueba y se ejecuta un ciclo completo
mediante las 4 fases de implementación.

Ejecutar con:
  python seed_demo.py
"""
import sys, os, logging
sys.path.insert(0, os.path.dirname(__file__))
logging.basicConfig(level=logging.WARNING)  # Silenciar DEBUG para demo limpia

from algorithms.motoa import MOTOAEngine
from algorithms.sequential_tuning import SequentialTuner
from models.node import NetworkNode, NodeType
from models.product import Product, ProductStatus
from models.task import Task, TaskStatus, OffloadingDecision
from models.user import UserProfile, UserRole
from services.notification import NotificationService
from services.payment_gateway import PaymentGateway, PaymentRequest
from services.recommendation import RecommendationService
from config import *


CYAN  = "\033[96m"
GREEN = "\033[92m"
YELLOW= "\033[93m"
RED   = "\033[91m"
BOLD  = "\033[1m"
RESET = "\033[0m"

def banner(text: str):
    print(f"\n{BOLD}{CYAN}{'═'*60}{RESET}")
    print(f"{BOLD}{CYAN}  {text}{RESET}")
    print(f"{BOLD}{CYAN}{'═'*60}{RESET}")

def ok(text):   print(f"  {GREEN} {RESET} {text}")
def info(text): print(f"  {YELLOW} {RESET} {text}")
def fail(text): print(f"  {RED} {RESET} {text}")


#  FASE 1 — Infraestructura Base
banner("FASE 1 — Infraestructura Base")

nodes = [
    NetworkNode(NodeType.EDGE, 5,  16, 0, 500,  115, 45,  250),
    NetworkNode(NodeType.EDGE, 5, 16, 0, 500, 120, 48, 380),
    NetworkNode(NodeType.CLOUD, 20, 64, 0, 1000, 500, 200, 0),
]
users = {
    "buyer1": UserProfile(role=UserRole.BUYER, name="Carlos Mendez", email="c.mendez@uni.co", category_preferences={"tecnología": 0.9, "moda": 0.5}),
    "buyer2": UserProfile(role=UserRole.BUYER, name="Ana Torres", email="a.torres@uni.co", category_preferences={"artesanía": 1.0, "alimentos": 0.7}),
    "emp1":   UserProfile(role=UserRole.ENTREPRENEUR, name="Luis Herrera", email="l.herrera@uni.co"),
    "emp2":   UserProfile(role=UserRole.ENTREPRENEUR, name="Sofia Ríos", email="s.rios@uni.co"),
}

products = [
    Product("Auriculares Bluetooth Pro", "tecnología", 89.99,  15, ProductStatus.NEW,     users["emp1"].user_id, description="Sonido HD 40h batería"),
    Product("Smartwatch FitPro X", "tecnología", 145.00, 8,  ProductStatus.REGULAR, users["emp1"].user_id, description="Monitor cardíaco GPS"),
    Product("Mochila Tejida Wayuu","artesanía",  55.00,  20, ProductStatus.NEW,     users["emp2"].user_id, description="100% artesanal"),
    Product("Bolso de Cuero Genuine", "accesorios", 220.00, 5,  ProductStatus.REGULAR, users["emp2"].user_id, description="Cuero colombiano"),
    Product("Café Especial Sierra Nevada","alimentos",  18.00,  50, ProductStatus.NEW,     users["emp1"].user_id, description="Tostado medio 250g"),
    Product("Camiseta Tie-Dye", "moda", 32.00, 30, ProductStatus.NEW, users["emp2"].user_id, description="Algodón orgánico"),
]

for n in nodes:
    ok(f"Nodo {n.node_type.value.upper():5s} | RAM {n.ram_total_gbit}Gb | "
       f"CPU {n.cpu_ghz}GHz | dist={n.distance_m}m")
ok(f"{len(users)} usuarios cargados  | {len(products)} productos cargados")

#  FASE 2 — Motor MOTOA
banner("FASE 2 — Motor MOTOA (Offloading Multi-Objetivo)")

motoa = MOTOAEngine(alpha=0.5, beta=0.5)
info(f"ν_threshold={NU_THRESHOLD} | p_cloud={P_CLOUD} | ε_tie={EPSILON_TIE}")
print()

test_tasks = [
    Task(users["buyer1"].user_id, 2e6,  0.5e6, 23.0, 500.0), # Búsqueda productos
    Task(users["buyer2"].user_id, 0.5e6, 0.1e6, 15.0, 200.0), # Ver catálogo
    Task(users["buyer1"].user_id, 10e6,  2e6,  26.0, 1000.0), # Carga imagen
    Task(users["buyer2"].user_id, 50e9, 1e6,   23.0, 0.001), # Deadline imposible
]

active_tasks = []
for i, task in enumerate(test_tasks, 1):
    global_q = sum(n.queue_length for n in nodes)
    srv_id, decision = motoa.process_task(task, nodes, global_q)

    if task.status == TaskStatus.DISCARDED:
        fail(f"Tarea {i}: DESCARTADA (deadline={task.deadline_ms}ms insuficiente)")
    elif task.status == TaskStatus.QUEUED:
        fail(f"Tarea {i}: ENCOLADA (sin recursos)")
    else:
        dest = decision.value.upper() if decision else "LOCAL"
        node = next((n for n in nodes if n.node_id == srv_id), None)
        ok(
            f"Tarea {i} -> {dest:5s} | "
            f"τ_total={task.tau_total_ms:.1f}ms | "
            f"E={task.energy_total_j:.5f}J | "
            f"server={srv_id[:8] if srv_id else 'N/A'}"
        )
        if task.tau_comp_ms:
            print(f" τ_ul={task.tau_uplink_ms:.3f}ms "
                  f"τ_comp={task.tau_comp_ms:.2f}ms "
                  f"τ_q={task.tau_queue_ms:.2f}ms")
        active_tasks.append(task)

print()
for n in nodes:
    bar_fill = int(n.utilization * 20)
    bar = "ll" * bar_fill + "-" * (20 - bar_fill)
    info(f"{n.node_type.value.upper():5s} RAM [{bar}] "
         f"{n.utilization*100:.1f}% | queue={n.queue_length}")

#  FASE 3 — Módulos Integrados

banner("FASE 3 — Módulos Integrados")

# Recomendaciones Pareto
print(f"\n{BOLD}  [A] Recomendaciones Personalizadas (Pareto + Visibilidad){RESET}")
recommender = RecommendationService()
results = recommender.get_recommendations(
    user = users["buyer1"],
    products = products,
    max_results = 5,
)
for r in results:
    front_tag = f"{GREEN}[PARETO]{RESET}" if r.is_pareto_front else f"[rank {r.pareto_rank}]"
    status_tag = f"{YELLOW}NEW{RESET}" if r.product.status == ProductStatus.NEW else "REG"
    ok(f"{status_tag} {front_tag} {r.product.name:30s} "
       f"${r.product.price:.2f} | rel={r.relevance_score:.2f}")

# Búsqueda Tabú
print(f"\n{BOLD}  [B] Búsqueda Tabú (excluir ya vistos){RESET}")
tabu_ids = [products[0].product_id, products[2].product_id]
search_results = recommender.search(
    products=products, query="", category=None,
    user=users["buyer2"], tabu_list=tabu_ids,
)
for r in search_results:
    ok(f"  {r.product.name}")

# Flujo de Pago
print(f"\n{BOLD} Flujo de Pago — Cinco pasos pasos con MOTOA Viability Check{RESET}")
notifier = NotificationService()
gateway  = PaymentGateway(motoa_engine=motoa, notification_svc=notifier)

pay_req = PaymentRequest(
    user_id = users["buyer1"].user_id,
    product_id= products[0].product_id,
    amount = products[0].price,
    payment_data = {"card_last4": "4242", "method": "tarjeta"},
    uplink_power_dbm = 23.0,
    deadline_ms = 5000.0,
)

record = gateway.process(
    request = pay_req,
    product = products[0],
    buyer = users["buyer1"],
    nodes = nodes,
    global_queue = sum(n.queue_length for n in nodes),
)

for step in record.audit_trail:
    status_color = GREEN if "OK" in step["step"] or "COMPLETADO" in step["step"] else YELLOW
    print(f" {status_color}[{step['step']:20s}]{RESET} {step['detail']}")

print(f"\n  {BOLD}Estado final: "
      f"{GREEN if record.status.value == 'completed' else RED}"
      f"{record.status.value.upper()}{RESET}")
print(f"  Stock restante de '{products[0].name}': {products[0].stock}")

# Notificaciones
inbox = notifier.get_inbox(users["buyer1"].user_id)
if inbox:
    ok(f"Notificación recibida: '{inbox[0].title}'")

#  FASE 4 — Ajuste Secuencial (Sequential Tuning)
banner("FASE 4 — Sequential Tuning (Proposición 1)")

tuner = SequentialTuner(motoa, max_iterations=100)

info(f"Tareas activas a optimizar: {len(active_tasks)}")
info(f"Coste inicial estimado del sistema:")
print()

result = tuner.optimize(active_tasks, nodes)

ok(f"Convergencia: {'SÍ' if result.converged else 'NO (max iter)'}")
ok(f"Iteraciones:  {result.iterations}")
ok(f"Coste inicial: {result.initial_cost:.6f}")
ok(f"Coste final:   {result.final_cost:.6f}")
pct = result.improvement_pct
ok(f"Mejora:        {pct:.2f}%" if pct == pct else "Mejora:        N/A (coste inicial no calculable pre-asignación)")
ok(f"Reasignaciones:{result.reassignments}")

finite_hist = [c for c in result.cost_history if c != float('inf') and c == c]
if len(finite_hist) > 1:
    print(f"\n  Evolución del coste:")
    max_c = max(finite_hist)
    for i, c in enumerate(finite_hist[:6]):
        bar = "ll" * int(c / max_c * 20) if max_c > 0 else ""
        print(f"  Iter {i:2d}: {bar} {c:.4f}")

#  RESUMEN FINAL
banner("RESUMEN FINAL DEL SISTEMA")

total_tasks = len(test_tasks)
processed   = sum(1 for t in test_tasks if t.status == TaskStatus.PROCESSING)
discarded   = sum(1 for t in test_tasks if t.status == TaskStatus.DISCARDED)

ok(f"Tareas procesadas:  {processed}/{total_tasks}")
ok(f"Tareas descartadas: {discarded}/{total_tasks}")
ok(f"Transacciones:      {len(gateway.all_records)}")
ok(f"Notificaciones:     {sum(len(notifier.get_inbox(u.user_id)) for u in users.values())}")

print(f"\n  {BOLD}Estado final de nodos:{RESET}")
for n in nodes:
    bar_fill = int(n.utilization * 20)
    bar = f"{GREEN}{'█' * bar_fill}{RESET}{'░' * (20 - bar_fill)}"
    print(f"  {n.node_type.value.upper():5s} [{bar}] "
          f"{n.utilization*100:.1f}% RAM | queue={n.queue_length}")

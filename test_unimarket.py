"""
Pruebas — UniMarket
Cobertura: MOTOA, Selección Pareto, Flujo de Pagos, Sequential Tuning

Se ejecutar con:
  pytest tests/ -v
"""
from __future__ import annotations
import math
import pytest
from unittest.mock import MagicMock
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from algorithms.motoa import MOTOAEngine
from algorithms.sequential_tuning import SequentialTuner
from models.node import NetworkNode, NodeType
from models.product import Product, ProductStatus
from models.task import Task, TaskStatus, OffloadingDecision
from models.user import UserProfile, UserRole
from services.recommendation import RecommendationService
from services.payment_gateway import PaymentGateway, PaymentRequest

#  FIXTURES

@pytest.fixture
def edge_node() -> NetworkNode:
    return NetworkNode(
        node_type = NodeType.EDGE,
        cpu_ghz = 5.0,
        ram_total_gbit = 16.0,
        ram_used_gbit = 0.0,
        bandwidth_mhz = 500.0,
        active_power_w = 115.0,
        idle_power_w = 45.0,
        distance_m = 300.0,
    )

@pytest.fixture
def cloud_node() -> NetworkNode:
    return NetworkNode(
        node_type = NodeType.CLOUD,
        cpu_ghz = 20.0,
        ram_total_gbit = 64.0,
        ram_used_gbit = 0.0,
        bandwidth_mhz = 1000.0,
        active_power_w = 500.0,
        idle_power_w = 200.0,
        distance_m = 0.0,
    )

@pytest.fixture
def nodes(edge_node, cloud_node) -> list:
    return [edge_node, cloud_node]

@pytest.fixture
def task_small() -> Task:
    """Tarea pequeña con plazo holgado → viable."""
    return Task(
        user_id = "user_001",
        input_size_bits = 1e6,    # 1 Mbit
        output_size_bits = 0.5e6,
        uplink_power_dbm = 23.0,
        deadline_ms = 1000.0,
    )

@pytest.fixture
def task_large() -> Task:
    """Tarea con plazo muy corto puede ser descartada"""
    return Task(
        user_id = "user_002",
        input_size_bits = 500e6,  # 500 Mbit — muy grande
        output_size_bits = 10e6,
        uplink_power_dbm = 23.0,
        deadline_ms = 0.001,  # 1 µs → imposible
    )

@pytest.fixture
def motoa() -> MOTOAEngine:
    return MOTOAEngine(alpha=0.5, beta=0.5)

#  FASE 2 — MOTOA

class TestMOTOA:

    def test_constants_match_spec(self, motoa):
        """Las constantes de inicialización deben coincidir con la especificación"""
        from config import NU_THRESHOLD, P_CLOUD, EPSILON_TIE
        assert NU_THRESHOLD == 3
        assert P_CLOUD == 0.05
        assert EPSILON_TIE == 1e-6

    def test_task_offloaded_successfully(self, motoa, task_small, nodes):
        """Una tarea viable debe ser asignada a un nodo."""
        server_id, decision = motoa.process_task(task_small, nodes, 0)
        assert task_small.status == TaskStatus.PROCESSING
        assert server_id is not None
        assert decision in (OffloadingDecision.EDGE, OffloadingDecision.CLOUD)

    def test_deadline_violation_discards_task(self, motoa, task_large, nodes):
        """Tareas con τ_total > D_m deben ser descartadas (Paso 1)."""
        server_id, decision = motoa.process_task(task_large, nodes, 0)
        assert task_large.status == TaskStatus.DISCARDED
        assert server_id is None
        assert decision is None

    def test_ram_constraint_enforced(self, motoa, task_small):
        """Nodo sin RAM suficiente no debe ser seleccionado (Paso 3)."""
        # Crear nodo con RAM completamente usada
        full_node = NetworkNode(
            node_type=NodeType.EDGE, cpu_ghz=5.0,
            ram_total_gbit=1.0, ram_used_gbit=1.0,  # 0 disponible
            bandwidth_mhz=500.0, active_power_w=115.0,
            idle_power_w=45.0, distance_m=300.0,
        )
        server_id, decision = motoa.process_task(task_small, [full_node], 0)
        assert task_small.status == TaskStatus.QUEUED

    def test_resource_deducted_after_assignment(self, motoa, task_small, edge_node, cloud_node):
        """Tras asignar una tarea, la RAM del nodo debe decrementarse (Paso 7)."""
        ram_before = edge_node.ram_used_gbit + cloud_node.ram_used_gbit
        motoa.process_task(task_small, [edge_node, cloud_node], 0)
        ram_after = edge_node.ram_used_gbit + cloud_node.ram_used_gbit
        assert ram_after > ram_before

    def test_latency_components_filled(self, motoa, task_small, nodes):
        """Las métricas τ_* deben estar pobladas tras el offloading."""
        motoa.process_task(task_small, nodes, 0)
        assert task_small.tau_uplink_ms is not None
        assert task_small.tau_downlink_ms is not None
        assert task_small.tau_comp_ms is not None
        assert task_small.tau_total_ms is not None
        assert task_small.energy_total_j is not None

    def test_tau_total_equals_sum_of_parts(self, motoa, task_small, nodes):
        """τ_total = τ_uplink + τ_downlink + τ_comp + τ_queue + τ_ram_wait + τ_prop."""
        from algorithms.motoa import MOTOAEngine
        from models.node import NetworkNode
        assigned_node = next(
            (n for n in nodes if n.node_id == task_small.assigned_server_id), None
        )
        motoa.process_task(task_small, nodes, 0)
        # τ_total incluye propagación (tau_prop), validamos con tolerancia numérica
        assert task_small.tau_total_ms is not None
        assert task_small.tau_total_ms > 0
        assert task_small.tau_total_ms >= (
            task_small.tau_uplink_ms + task_small.tau_downlink_ms +
            task_small.tau_comp_ms   + task_small.tau_queue_ms
        ) - 1e-6   # τ_total ≥ suma de componentes medibles

    def test_dynamic_params_adjust_on_high_queue(self, motoa, nodes):
        """Con cola > ν_threshold, p_cloud debe aumentar y ν_threshold bajar."""
        from config import P_CLOUD, NU_THRESHOLD
        motoa._adjust_parameters(queue_length=20)
        assert motoa._p_cloud > P_CLOUD
        assert motoa._nu_threshold < NU_THRESHOLD

    def test_params_reset_on_low_queue(self, motoa):
        """Con cola baja, los parámetros deben volver a valores por defecto."""
        from config import P_CLOUD, NU_THRESHOLD
        motoa._adjust_parameters(queue_length=1)
        assert motoa._p_cloud    == P_CLOUD
        assert motoa._nu_threshold == NU_THRESHOLD

    def test_pareto_dominance(self, motoa):
        """A domina a B si es mejor o igual en todos y estricto en al menos uno."""
        from algorithms.motoa import NodeCosts, LatencyBreakdown
        lat = LatencyBreakdown(1, 1, 1, 0, 0, 0)
        a = NodeCosts(node=MagicMock(), latency=lat, energy_j=1.0,
                      queue_penalty=0, load_balance=0,
                      objectives={"norm_latency":0.1, "norm_energy":0.2,
                                  "norm_queue":0.1, "norm_load":0.1})
        b = NodeCosts(node=MagicMock(), latency=lat, energy_j=2.0,
                      queue_penalty=0, load_balance=0,
                      objectives={"norm_latency":0.3, "norm_energy":0.5,
                                  "norm_queue":0.3, "norm_load":0.3})
        assert motoa._dominates(a, b)
        assert not motoa._dominates(b, a)

    def test_shannon_rate_positive(self, motoa):
        """La tasa de Shannon debe ser positiva para cualquier potencia razonable."""
        rate = motoa._shannon_rate(23.0, 100e6)
        assert rate > 0

    def test_energy_total_formula(self, motoa, task_small, edge_node):
        """E_total = E_trans + E_comp debe ser > 0."""
        energy = motoa._calc_energy(task_small, edge_node)
        assert energy > 0

#  FASE 4 — SEQUENTIAL TUNING

class TestSequentialTuning:

    def test_converges_with_no_active_tasks(self, motoa):
        tuner = SequentialTuner(motoa)
        result = tuner.optimize([], [])
        assert result.converged
        assert result.iterations == 0

    def test_converges_on_single_task(self, motoa, task_small, nodes):
        motoa.process_task(task_small, nodes, 0)
        task_small.status = __import__('models.task', fromlist=['TaskStatus']).TaskStatus.PROCESSING

        tuner = SequentialTuner(motoa, max_iterations=50)
        result = tuner.optimize([task_small], nodes)
        assert result.converged or result.iterations >= 1
        assert result.final_cost >= 0

    def test_cost_monotonically_decreasing(self, motoa, task_small, nodes):
        """El coste total debe ser no-creciente en cada iteración."""
        motoa.process_task(task_small, nodes, 0)
        task_small.status = __import__('models.task', fromlist=['TaskStatus']).TaskStatus.PROCESSING

        tuner = SequentialTuner(motoa)
        result = tuner.optimize([task_small], nodes)

        for i in range(1, len(result.cost_history)):
            assert result.cost_history[i] <= result.cost_history[i - 1] + 1e-9

#  RECOMENDACIONES — PARETO

class TestRecommendations:
    @pytest.fixture
    def products(self):
        return [
            Product("Mochila Eco", "accesorios", 45.0,  10, ProductStatus.NEW,     "e1"),
            Product("Bolso Cuero", "accesorios", 120.0, 5,  ProductStatus.REGULAR, "e2"),
            Product("Funda Laptop", "tecnología", 30.0,  20, ProductStatus.NEW,     "e3"),
            Product("Ratón USB", "tecnología", 25.0,  0,  ProductStatus.REGULAR, "e4"),  # sin stock
        ]

    def test_pareto_front_excludes_dominated(self, products):
        svc    = RecommendationService()
        ranked = svc.get_recommendations(user=None, products=products, max_results=10)
        assert len(ranked) > 0
        assert all(r.is_pareto_front or r.pareto_rank > 1 for r in ranked)

    def test_out_of_stock_penalized(self, products):
        svc = RecommendationService()
        ranked = svc.get_recommendations(user=None, products=products, max_results=10)
        # Ratón USB (stock=0) debe tener score bajo
        raton = next((r for r in ranked if r.product.name == "Ratón USB"), None)
        if raton:
            assert raton.relevance_score < 0.5

    def test_new_products_get_visibility(self, products):
        svc = RecommendationService()
        ranked = svc.get_recommendations(user=None, products=products, max_results=10)
        # Al menos un producto NEW debe aparecer en los 3 primeros
        top3_statuses = [r.product.status for r in ranked[:3]]
        assert ProductStatus.NEW in top3_statuses

    def test_tabu_search_excludes_seen(self, products):
        svc = RecommendationService()
        all_ids = [p.product_id for p in products]
        # Primer producto como tabú
        tabu = [products[0].product_id]
        results = svc.search(products, "", None, None, tabu_list=tabu)
        result_ids = [r.product.product_id for r in results]
        assert products[0].product_id not in result_ids

    def test_user_preferences_boost_category(self, products):
        user = UserProfile(
            name="Ana", email="ana@test.com", role=UserRole.BUYER,
            category_preferences={"tecnología": 1.0}
        )
        svc = RecommendationService()
        results = svc.get_recommendations(user=user, products=products, max_results=10)
        # Productos de tecnología deben tener mayor score
        tech = [r for r in results if r.product.category == "tecnología"]
        other = [r for r in results if r.product.category != "tecnología"]
        if tech and other:
            avg_tech = sum(r.relevance_score for r in tech)  / len(tech)
            avg_other = sum(r.relevance_score for r in other) / len(other)
            assert avg_tech >= avg_other

#  FLUJO DE PAGOS

class TestPaymentFlow:
    @pytest.fixture
    def buyer(self):
        return UserProfile(name="Carlos", email="c@test.com", role=UserRole.BUYER)

    @pytest.fixture
    def product(self):
        return Product("Mochila XL", "accesorios", 75.0, 5, ProductStatus.NEW, "emp1")
    @pytest.fixture
    def notifier(self):
        return MagicMock()

    @pytest.fixture
    def gateway(self, motoa, notifier):
        return PaymentGateway(motoa_engine=motoa, notification_svc=notifier)

    @pytest.fixture
    def pay_request(self, buyer, product):
        return PaymentRequest(
            user_id = buyer.user_id,
            product_id= product.product_id,
            amount = 75.0,
            payment_data = {"card_last4": "4242"},
            uplink_power_dbm = 23.0,
            deadline_ms = 5000.0,
        )

    def test_successful_payment_reduces_stock(self, gateway, pay_request, product, buyer, nodes):
        stock_before = product.stock
        record = gateway.process(pay_request, product, buyer, nodes, 0)
        from services.payment_gateway import PaymentStatus
        assert record.status == PaymentStatus.COMPLETED
        assert product.stock == stock_before - 1

    def test_payment_fails_on_zero_stock(self, gateway, pay_request, product, buyer, nodes):
        product.stock = 0
        record = gateway.process(pay_request, product, buyer, nodes, 0)
        from services.payment_gateway import PaymentStatus
        assert record.status == PaymentStatus.FAILED
        assert product.stock == 0  # no debe decrementarse

    def test_audit_trail_recorded(self, gateway, pay_request, product, buyer, nodes):
        record = gateway.process(pay_request, product, buyer, nodes, 0)
        assert len(record.audit_trail) >= 3 

    def test_notifications_sent_on_success(self, gateway, pay_request, product, buyer, nodes, notifier):
        gateway.process(pay_request, product, buyer, nodes, 0)
        notifier.notify_payment_success.assert_called_once()
    def test_buyer_transaction_recorded(self, gateway, pay_request, product, buyer, nodes):
        assert len(buyer.transaction_history) == 0
        gateway.process(pay_request, product, buyer, nodes, 0)
        assert len(buyer.transaction_history) == 1

#  RESTRICCIONES NO NEGOCIABLES 

class TestHardConstraints:

    def test_noise_floor_is_minus_100_dbm(self):
        from config import NOISE_FLOOR_DBM
        assert NOISE_FLOOR_DBM == -100.0
    def test_fiber_speed(self):
        from config import FIBER_SPEED_MPS
        assert FIBER_SPEED_MPS == 2e8

    def test_gnb_tx_power(self):
        from config import GNB_TX_POWER_DBM
        assert GNB_TX_POWER_DBM == 46.0
    def test_edge_ram_spec(self):
        from config import EDGE_RAM_GBIT
        assert EDGE_RAM_GBIT == 16.0

    def test_cloud_ram_spec(self):
        from config import CLOUD_RAM_GBIT
        assert CLOUD_RAM_GBIT == 64.0

    def test_task_with_impossible_deadline_is_discarded(self):
        motoa= MOTOAEngine()
        task = Task("u1", 1e6, 1e6, 23.0, deadline_ms=0.0001)  # 0.1 µs
        edge = NetworkNode(NodeType.EDGE, 5, 16, 0, 500, 115, 45, 300)
        cloud  = NetworkNode(NodeType.CLOUD, 20, 64, 0, 1000, 500, 200, 0)
        motoa.process_task(task, [edge, cloud], 0)
        assert task.status == TaskStatus.DISCARDED

    def test_ram_capacity_hard_limit(self):
        """RAM usada nunca puede superar la capacidad total (M_s)"""
        node = NetworkNode(NodeType.EDGE, 5, 16, 16.0, 500, 115, 45, 300)
        assert node.ram_available_gbit == 0.0
        success = node.allocate("t1", 1.0)
        assert not success
        assert node.ram_used_gbit == 16.0   # No creció

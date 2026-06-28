"""Joint Recommendation-Offloading Framework (JROF).

Orquesta una optimización alternante entre `RecommendationService` y
`MOTOAEngine` para producir una política conjunta (R*, O*).
"""

from __future__ import annotations

import copy
import math
import time
import uuid
from dataclasses import dataclass, field
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional

from algorithms.motoa import MOTOAEngine
from models.node import NetworkNode
from models.product import Product, ProductStatus
from models.task import OffloadingDecision, Task, TaskStatus
from models.user import UserProfile
from recommendation import RecommendationService, RankedProduct


@dataclass
class InfrastructureConfidence:
    snapshot_age_s: float
    utilization_mean: float
    queue_variance: float
    queue_pressure: float
    confidence: float


@dataclass
class JointState:
    user_id: str
    query: str
    category: Optional[str]
    max_results: int
    product_count: int
    node_count: int
    infra_confidence: InfrastructureConfidence


@dataclass
class JROFIterationTrace:
    iteration: int
    budget: int
    selected_node_id: Optional[str]
    selected_node_type: Optional[str]
    recommendation_ids: List[str] = field(default_factory=list)
    demand_bits: float = 0.0
    queue_pressure: int = 0
    converged: bool = False


@dataclass
class JROFResult:
    policy_id: str
    converged: bool
    iterations: int
    state: JointState
    selected_node_id: Optional[str]
    selected_node_type: Optional[str]
    selected_products: List[dict]
    policy: Dict[str, Any]
    feedback_trace: List[dict] = field(default_factory=list)


class JROFEngine:
    """Alterna recomendación y offloading hasta estabilizar la política."""

    def __init__(
        self,
        recommender: RecommendationService,
        motoa: MOTOAEngine,
        max_iterations: int = 3,
        convergence_epsilon: float = 1e-3,
    ):
        self.recommender = recommender
        self.motoa = motoa
        self.max_iterations = max_iterations
        self.convergence_epsilon = convergence_epsilon

    def optimize(
        self,
        user: Optional[UserProfile],
        products: List[Product],
        nodes: List[NetworkNode],
        query: str = "",
        category: Optional[str] = None,
        max_results: int = 10,
        deadline_ms: float = 5000.0,
        snapshot_age_s: float = 0.0,
        tabu_ids: Optional[List[str]] = None,
    ) -> JROFResult:
        if not products or not nodes:
            state = self._build_state(
                user=user,
                products=products,
                nodes=nodes,
                query=query,
                category=category,
                max_results=max_results,
                snapshot_age_s=snapshot_age_s,
            )
            return JROFResult(
                policy_id=str(uuid.uuid4()),
                converged=True,
                iterations=0,
                state=state,
                selected_node_id=None,
                selected_node_type=None,
                selected_products=[],
                policy={"R": [], "O": None},
                feedback_trace=[],
            )

        state = self._build_state(
            user=user,
            products=products,
            nodes=nodes,
            query=query,
            category=category,
            max_results=max_results,
            snapshot_age_s=snapshot_age_s,
        )

        current_node: Optional[NetworkNode] = None
        current_ranking: List[RankedProduct] = []
        trace: List[JROFIterationTrace] = []
        previous_signature: Optional[tuple] = None
        converged = False

        for iteration in range(1, self.max_iterations + 1):
            budget = self._recommendation_budget(current_node, state, max_results)
            ranking = self._rank_products(
                user=user,
                products=products,
                query=query,
                category=category,
                max_results=budget,
                tabu_ids=tabu_ids,
            )

            demand_bits, queue_pressure = self._estimate_demand(ranking, products, state)
            probe_nodes = copy.deepcopy(nodes)
            control_task = self._build_control_task(user=user, demand_bits=demand_bits, deadline_ms=deadline_ms)
            global_queue = self._joint_queue_pressure(nodes, queue_pressure, state)
            selected_server_id, _ = self.motoa.process_task(control_task, probe_nodes, global_queue)
            selected_node = self._find_node(probe_nodes, selected_server_id)

            current_signature = (
                selected_server_id,
                tuple(r.product.product_id for r in ranking[: max_results]),
            )
            trace.append(
                JROFIterationTrace(
                    iteration=iteration,
                    budget=budget,
                    selected_node_id=selected_server_id,
                    selected_node_type=selected_node.node_type.value if selected_node else None,
                    recommendation_ids=[r.product.product_id for r in ranking[: max_results]],
                    demand_bits=demand_bits,
                    queue_pressure=global_queue,
                    converged=previous_signature == current_signature,
                )
            )

            current_node = selected_node or current_node
            current_ranking = ranking

            if previous_signature == current_signature:
                converged = True
                break
            previous_signature = current_signature

        selected_products = [r.to_dict() for r in current_ranking[:max_results]]
        selected_node_id = current_node.node_id if current_node else None
        selected_node_type = current_node.node_type.value if current_node else None

        return JROFResult(
            policy_id=str(uuid.uuid4()),
            converged=converged,
            iterations=len(trace),
            state=state,
            selected_node_id=selected_node_id,
            selected_node_type=selected_node_type,
            selected_products=selected_products,
            policy={"R": [p["product_id"] for p in selected_products], "O": selected_node_id},
            feedback_trace=[trace_item.__dict__ for trace_item in trace],
        )

    def _build_state(
        self,
        user: Optional[UserProfile],
        products: List[Product],
        nodes: List[NetworkNode],
        query: str,
        category: Optional[str],
        max_results: int,
        snapshot_age_s: float,
    ) -> JointState:
        confidence = self._compute_confidence(nodes, snapshot_age_s)
        return JointState(
            user_id=user.user_id if user else "anonymous",
            query=query,
            category=category,
            max_results=max_results,
            product_count=len(products),
            node_count=len(nodes),
            infra_confidence=confidence,
        )

    def _compute_confidence(self, nodes: List[NetworkNode], snapshot_age_s: float) -> InfrastructureConfidence:
        if not nodes:
            return InfrastructureConfidence(snapshot_age_s, 0.0, 0.0, 0.0, 0.0)

        utilizations = [n.utilization for n in nodes]
        queue_lengths = [n.queue_length for n in nodes]
        utilization_mean = mean(utilizations)
        queue_variance = pstdev(queue_lengths) ** 2 if len(queue_lengths) > 1 else 0.0
        queue_pressure = min(1.0, sum(queue_lengths) / max(1, len(nodes) * 5))
        freshness = math.exp(-snapshot_age_s / 5.0)
        instability = min(1.0, 0.5 * utilization_mean + 0.3 * queue_pressure + 0.2 * min(1.0, queue_variance / 10.0))
        confidence = max(0.0, min(1.0, freshness * (1.0 - instability)))
        return InfrastructureConfidence(
            snapshot_age_s=snapshot_age_s,
            utilization_mean=utilization_mean,
            queue_variance=queue_variance,
            queue_pressure=queue_pressure,
            confidence=confidence,
        )

    def _recommendation_budget(
        self,
        node: Optional[NetworkNode],
        state: JointState,
        max_results: int,
    ) -> int:
        if node is None:
            return max(1, min(max_results, state.max_results))

        capacity_ratio = node.ram_available_gbit / max(node.ram_total_gbit, 1e-9)
        queue_factor = 1.0 / max(1.0, 1.0 + node.queue_length)
        confidence = state.infra_confidence.confidence
        scale = 0.35 + 0.35 * confidence + 0.20 * capacity_ratio + 0.10 * queue_factor
        budget = max(1, int(round(max_results * max(0.2, min(1.0, scale)))))
        return min(max_results, budget)

    def _rank_products(
        self,
        user: Optional[UserProfile],
        products: List[Product],
        query: str,
        category: Optional[str],
        max_results: int,
        tabu_ids: Optional[List[str]],
    ) -> List[RankedProduct]:
        if query or category or tabu_ids:
            return self.recommender.search(
                products=products,
                query=query,
                category=category,
                user=user,
                max_results=max_results,
                tabu_list=tabu_ids,
            )
        return self.recommender.get_recommendations(user=user, products=products, max_results=max_results)

    def _estimate_demand(
        self,
        ranking: List[RankedProduct],
        products: List[Product],
        state: JointState,
    ) -> tuple[float, int]:
        visible = max(1, len(ranking))
        catalog = max(1, len(products))
        demand_bits = 250_000.0 + (visible * 60_000.0) + (catalog * 2_500.0)
        demand_bits *= 1.0 + (1.0 - state.infra_confidence.confidence)
        queue_pressure = int(round(catalog * (0.15 + (1.0 - state.infra_confidence.confidence) * 0.35)))
        return demand_bits, queue_pressure

    def _build_control_task(self, user: Optional[UserProfile], demand_bits: float, deadline_ms: float) -> Task:
        return Task(
            user_id=user.user_id if user else "anonymous",
            input_size_bits=demand_bits,
            output_size_bits=max(50_000.0, demand_bits * 0.10),
            uplink_power_dbm=23.0,
            deadline_ms=deadline_ms,
        )

    def _joint_queue_pressure(self, nodes: List[NetworkNode], extra_pressure: int, state: JointState) -> int:
        base_pressure = sum(n.queue_length for n in nodes)
        confidence_penalty = int(round((1.0 - state.infra_confidence.confidence) * len(nodes)))
        return base_pressure + extra_pressure + confidence_penalty

    @staticmethod
    def _find_node(nodes: List[NetworkNode], node_id: Optional[str]) -> Optional[NetworkNode]:
        if not node_id:
            return None
        return next((n for n in nodes if n.node_id == node_id), None)

"""
Optimización por ajuste secuencial
Modelo multi-usuario

Revisa secuencialmente cada tarea activa para determinar si cambiar su
decisión de local vs Edge vs Cloud reduce el coste total
del sistema sin violar restricciones, alcanzando así el óptimo local
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from algorithms.motoa import MOTOAEngine
from config import P_CLOUD, EPSILON_TIE
from models.node import NetworkNode, NodeType
from models.task import OffloadingDecision, Task, TaskStatus

logger = logging.getLogger(__name__)

@dataclass
class TuningResult:
    converged: bool
    iterations: int
    final_cost: float
    initial_cost: float
    cost_history: List[float] = field(default_factory=list)
    reassignments: int = 0

    @property
    def improvement_pct(self) -> float:
        if self.initial_cost == 0:
            return 0.0
        return (self.initial_cost - self.final_cost) / self.initial_cost * 100


class SequentialTuner:
    """
    Optimizador iterativo post-MOTOA.

    Bucle principal:
      For each task T_m in {active_tasks}:
        For each alternative decision d ∈ {EDGE, CLOUD}:
          Si coste(T_m, d) < coste_actual(T_m) sin violar restricciones:
            → Reasignar T_m a la nueva decisión
      Repetir hasta convergencia o max_iterations.
    """

    CONVERGENCE_DELTA = 1e-4

    def __init__(self, motoa: MOTOAEngine, max_iterations: int = 100):
        self.motoa          = motoa
        self.max_iterations = max_iterations

    def optimize(
        self,
        tasks: List[Task],
        nodes: List[NetworkNode],
    ) -> TuningResult:
        """
        Ejecuta el ajuste secuencial sobre las tareas activas
        Modifica in-place las tareas y los nodos
        """
        active = [t for t in tasks if t.status == TaskStatus.PROCESSING]
        if not active:
            logger.info("SeqTuning: Sin tareas activas.")
            return TuningResult(converged=True, iterations=0,
                                final_cost=0.0, initial_cost=0.0)

        initial_cost = self._system_cost(active, nodes)
        current_cost = initial_cost
        history      = [current_cost]
        reassignments = 0

        logger.info(
            "SeqTuning START: %d tareas | coste inicial = %.6f",
            len(active), initial_cost
        )

        for iteration in range(1, self.max_iterations + 1):
            improved = False

            for task in active:
                gain, new_node, new_decision = self._best_alternative(task, nodes)

                if gain > self.CONVERGENCE_DELTA and new_node is not None:
                    self._reassign(task, new_node, new_decision, nodes)
                    current_cost -= gain
                    reassignments += 1
                    improved = True
                    logger.debug(
                        "  Iter %d: %s → %s (ganancia=%.6f)",
                        iteration, task.task_id[:8],
                        new_decision.value if new_decision else "?", gain
                    )

            new_cost = self._system_cost(active, nodes)
            history.append(new_cost)
            delta = abs(current_cost - new_cost)
            current_cost = new_cost

            if not improved or delta < self.CONVERGENCE_DELTA:
                logger.info(
                    "SeqTuning CONVERGED en iteración %d | coste final = %.6f",
                    iteration, current_cost
                )
                return TuningResult(
                    converged = True,
                    iterations = iteration,
                    final_cost = current_cost,
                    initial_cost = initial_cost,
                    cost_history = history,
                    reassignments= reassignments,
                )

        logger.warning("SeqTuning: máx. iteraciones alcanzadas (%d)", self.max_iterations)
        return TuningResult(
            converged = False,
            iterations = self.max_iterations,
            final_cost = current_cost,
            initial_cost = initial_cost,
            cost_history = history,
            reassignments= reassignments,
        )

    # ──────────────────────────────────────────────────────────────────────────

    def _best_alternative(
        self, task: Task, nodes: List[NetworkNode]
    ) -> Tuple[float, Optional[NetworkNode], Optional[OffloadingDecision]]:
        """
        La funcion busca la alternativa de offloading que minimice el coste individual.
        Respeta restricciones de RAM (no puede exceder M_s disponible)

        Returna:
            (ganancia, nodo_candidato, decision)
        """
        current_cost = self._task_cost(task, nodes)
        best_gain = 0.0
        best_node = None
        best_decision = None

        for decision in [OffloadingDecision.EDGE, OffloadingDecision.CLOUD]:
            if decision == task.offloading_decision:
                continue

            candidates = self._nodes_for_decision(decision, nodes)
            for node in candidates:
                ram_needed = task.input_size_bits / 8e9
                # No asignar si no hay RAM — ni siquiera temporalmente
                if node.ram_available_gbit < ram_needed:
                    continue

                sim_cost = self._simulate_cost(task, node)
                gain = current_cost - sim_cost

                if gain > best_gain + EPSILON_TIE:
                    best_gain = gain
                    best_node = node
                    best_decision = decision

        return best_gain, best_node, best_decision

    def _nodes_for_decision(
        self, decision: OffloadingDecision, nodes: List[NetworkNode]
    ) -> List[NetworkNode]:
        target = NodeType.CLOUD if decision == OffloadingDecision.CLOUD else NodeType.EDGE
        return [n for n in nodes if n.node_type == target]

    def _simulate_cost(self, task: Task, node: NetworkNode) -> float:
        """Coste C_j simulado para una asignación hipotética."""
        lat = self.motoa._calc_latency(task, node).total_ms
        eng = self.motoa._calc_energy(task, node)
        qpen = self.motoa._queue_penalty(node)
        cloud_pen = P_CLOUD if node.node_type == NodeType.CLOUD else 0.0
        return self.motoa.beta * lat + self.motoa.alpha * eng + 0.1 * qpen + cloud_pen

    def _task_cost(self, task: Task, nodes: List[NetworkNode]) -> float:
        """Coste actual de una tarea según su asignación vigente."""
        if not task.assigned_server_id:
            return float("inf")
        node = next((n for n in nodes if n.node_id == task.assigned_server_id), None)
        if not node:
            return float("inf")
        return self._simulate_cost(task, node)

    def _system_cost(self, tasks: List[Task], nodes: List[NetworkNode]) -> float:
        """Coste total del sistema = suma de costes individuales"""
        return sum(self._task_cost(t, nodes) for t in tasks)

    def _reassign(
        self,
        task: Task,
        new_node: NetworkNode,
        new_decision: OffloadingDecision,
        nodes: List[NetworkNode],
    ) -> None:
        """Reasigna la tarea: libera recursos en el nodo anterior y asigna en el nuevo"""
        ram = task.input_size_bits / 8e9  # bits → Gbit

        # Liberar recursos en nodo anterior
        old_node = next(
            (n for n in nodes if n.node_id == task.assigned_server_id), None
        )
        if old_node:
            old_node.release(task.task_id, ram)

        # Asignar en nuevo nodo
        new_node.allocate(task.task_id, ram)

        # Actualizar tarea
        task.offloading_decision = new_decision
        task.assigned_server_id = new_node.node_id

        # Recalcular métricas
        lat = self.motoa._calc_latency(task, new_node)
        task.tau_total_ms = lat.total_ms
        task.energy_total_j = self.motoa._calc_energy(task, new_node)

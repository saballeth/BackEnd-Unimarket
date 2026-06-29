"""
MOTOA — Multi-Objective Task Offloading Algorithm
Motor de decisión central

Se Iimplementa mediante los 7 pasos de offloading de tareas descritos 
  1. Validación de plazo 
  2. Ajuste dinámico de parámetros
  3. Filtro de viabilidad 
  4. Normalización de costes
  5. Calculo de coste compuesto C_j
  6. Selección de Pareto
  7. Actualización de recursos

Cuatro Objetivos:
  - Latencia τ_total
  - Energía  E_total
  - Penalización por cola τ_queue
  - Equilibrio de carga std RAM
"""
from __future__ import annotations
import math
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import numpy as np

from config import (
    DEFAULT_ALPHA, DEFAULT_BETA,
    EPSILON_TIE, GNB_BANDWIDTH_MHZ, GNB_TX_POWER_DBM,
    NOISE_FLOOR_DBM, NU_THRESHOLD, P_CLOUD,
    FIBER_SPEED_MPS, LIGHT_SPEED_MPS, CLOUD_FIBER_DISTANCE_M,
)
from models.node import NetworkNode, NodeType
from models.task import OffloadingDecision, Task, TaskStatus

logger = logging.getLogger(__name__)

# Estructuras

@dataclass
class LatencyBreakdown:
    tau_uplink_ms: float
    tau_downlink_ms: float
    tau_comp_ms: float
    tau_queue_ms: float
    tau_ram_wait_ms: float
    tau_prop_ms: float

    @property
    def total_ms(self) -> float:
        return (
            self.tau_uplink_ms + self.tau_downlink_ms +
            self.tau_comp_ms   + self.tau_queue_ms    +
            self.tau_ram_wait_ms + self.tau_prop_ms
        )

@dataclass
class NodeCosts:
    node: NetworkNode
    latency: LatencyBreakdown
    energy_j: float
    queue_penalty: float
    load_balance: float
    composite: float = 0.0
    objectives: Dict[str, float] = None

    def __post_init__(self):
        if self.objectives is None:
            self.objectives = {}

# ─── Motor MOTOA
class MOTOAEngine:
    """
    Motor de offloading multi-objetivo para la arquitectura 5G Fog-Cloud
    de UniMarket. Resuelve el problema no-convexo de asignación de recursos
    en tiempo real
    """

    def __init__(self, alpha: float = DEFAULT_ALPHA, beta: float = DEFAULT_BETA):
        """
        Args:
            alpha: Peso para el objetivo de Energía (α)
            beta: Peso para el objetivo de Latencia (β)
        """
        self.alpha = alpha
        self.beta  = beta

        # Parámetros
        self._nu_threshold = NU_THRESHOLD
        self._p_cloud = P_CLOUD

    #  PIPELINE PRINCIPAL

    def process_task(
        self,
        task: Task,
        nodes: List[NetworkNode],
        global_queue_length: int,
    ) -> Tuple[Optional[str], Optional[OffloadingDecision]]:
        """
        Ejecuta el pipeline MOTOA completo para una tarea.

        Returns:
            (server_id, decision) → si la tarea fue asignada
            (None, None) → si fue descartada o encolada
        """
        logger.debug("MOTOA ▶ Procesando %s", task)

        # Validación de Plazo 
        if not nodes:
            task.status = TaskStatus.DISCARDED
            logger.warning("Paso 1 FAIL: Sin nodos disponibles para %s", task.task_id)
            return None, None

        min_latency = self._estimate_min_latency(task, nodes)
        if min_latency > task.deadline_ms:
            task.status = TaskStatus.DISCARDED
            logger.info(
                "Paso 1 FAIL: τ_min=%.2fms > D_m=%.2fms → descartada %s",
                min_latency, task.deadline_ms, task.task_id
            )
            return None, None

        # Ajuste de los Parámetros de arriba
        self._adjust_parameters(global_queue_length)

        # Filtro de viabilidad 
        feasible = self._filter_feasible(task, nodes)
        if not feasible:
            task.status = TaskStatus.QUEUED
            logger.info("Paso 3 FAIL: Sin nodos viables → encolada %s", task.task_id)
            return None, None

        # Normalización + coste compuesto 
        costs = self._compute_costs(task, feasible)

        # Selección de Pareto
        selected = self._pareto_select(costs)
        if not selected:
            task.status = TaskStatus.QUEUED
            return None, None

        # Por acá se hace la actualización de recursos
        ram_needed = task.input_size_bits / 8e9   # bits → Gbit
        selected.node.allocate(task.task_id, ram_needed)

        # Guardar métricas en la tarea
        task.tau_uplink_ms = selected.latency.tau_uplink_ms
        task.tau_downlink_ms = selected.latency.tau_downlink_ms
        task.tau_comp_ms = selected.latency.tau_comp_ms
        task.tau_queue_ms = selected.latency.tau_queue_ms
        task.tau_ram_wait_ms = selected.latency.tau_ram_wait_ms
        task.tau_total_ms = selected.latency.total_ms
        task.energy_total_j = selected.energy_j

        decision = (
            OffloadingDecision.CLOUD
            if selected.node.node_type == NodeType.CLOUD
            else OffloadingDecision.EDGE
        )

        # Persistir decisión directamente en la tarea
        task.assigned_server_id  = selected.node.node_id
        task.offloading_decision = decision
        task.status              = TaskStatus.PROCESSING
        logger.info(
            "Paso 7 OK: %s → %s | τ=%.2fms | E=%.4fJ",
            task.task_id[:8], selected.node.node_id[:8],
            task.tau_total_ms, task.energy_total_j
        )
        return selected.node.node_id, decision

    # VALIDACIÓN DE PLAZO
    def _estimate_min_latency(self, task: Task, nodes: List[NetworkNode]) -> float:
        """Estima la latencia mínima posible usando el nodo con cola más corta."""
        best = min(nodes, key=lambda n: n.queue_length)
        return self._calc_latency(task, best).total_ms

    # AJUSTE DE PARÁMETROS
    def _adjust_parameters(self, queue_length: int) -> None:
        """
        Actualiza ν_threshold y p_cloud según la longitud de la cola global.
        Si la cola supera ν_threshold → se favorece el Cloud para descongestionar
        """
        if queue_length > self._nu_threshold:
            # Incremento proporcional de la penalización cloud y reducción del umbral
            self._p_cloud      = min(0.4, P_CLOUD + queue_length * 0.01)
            self._nu_threshold = max(1, NU_THRESHOLD - queue_length // 4)
        else:
            self._p_cloud      = P_CLOUD
            self._nu_threshold = NU_THRESHOLD

        logger.debug(
            "Paso 2: ν_threshold=%d, p_cloud=%.4f (queue=%d)",
            self._nu_threshold, self._p_cloud, queue_length
        )

    # FILTRO DE VIABILIDAD
    def _filter_feasible(self, task: Task, nodes: List[NetworkNode]) -> List[NetworkNode]:
        """
        Filtra nodos donde:
          (a) RAM disponible M_s ≥ I_m (en Gbit)
          (b) SINR ≥ umbral mínimo de transmisión confiable
        """
        required_ram = task.input_size_bits / 8e9  # bits → Gbit
        feasible = []

        for node in nodes:
            # Restricción RAM 
            if node.ram_available_gbit < required_ram:
                logger.debug(
                    "  Nodo %s descartado: RAM %.2f < %.2f Gbit",
                    node.node_id[:8], node.ram_available_gbit, required_ram
                )
                continue

            # Restricción SINR 
            sinr_db = self._calc_sinr(task.uplink_power_dbm, node)
            if sinr_db < self._sinr_threshold():
                logger.debug(
                    "  Nodo %s descartado: SINR %.2f dB < umbral",
                    node.node_id[:8], sinr_db
                )
                continue

            feasible.append(node)

        return feasible

    def _calc_sinr(self, tx_power_dbm: float, node: NetworkNode) -> float:
        """
        SINR = P_recibida / P_ruido (dB)
        Piso de ruido: NOISE_FLOOR_DBM = -100 dBm (Sección 8)
        Modelo de pérdida de trayecto simplificado LOS/NLOS según distancia.
        """
        tx_mw    = self._dbm_to_mw(tx_power_dbm)
        noise_mw = self._dbm_to_mw(NOISE_FLOOR_DBM)

        if node.node_type == NodeType.EDGE:
            # Modelo NLOS urbano simplificado (3GPP UMi)
            d = max(node.distance_m, 1.0)
            fc_ghz = 3.5  # Frecuencia 5G NR (GHz)
            pl_db = 35.3 * math.log10(d) + 22.4 + 21.3 * math.log10(fc_ghz)
            pl_linear = 10 ** (pl_db / 10)
        else:
            # Cloud: siempre a través del gNB 
            # La señal ya llega con ganancia de antena Massive MIMO
            pl_linear = 100.0  # Pérdida reducida por beamforming 64 antenas

        rx_power_mw = tx_mw / pl_linear
        # Ganancia Massive MIMO: 10*log10(64) ≈ 18 dB
        rx_power_mw *= GNB_MIMO_ANTENNAS_GAIN()

        sinr_linear = rx_power_mw / noise_mw
        return 10 * math.log10(max(sinr_linear, 1e-12))

    @staticmethod
    def _sinr_threshold() -> float:
        """Umbral mínimo SINR para transmisión confiable 5G NR (dB)."""
        return -5.0  # QPSK 1/6 ≈ -6 dB en 5G NR

    # NORMALIZACIÓN + COSTE COMPUESTO

    def _compute_costs(self, task: Task, nodes: List[NetworkNode]) -> List[NodeCosts]:
        """
        Calcula T_total, E_total, penalización de cola y equilibrio de carga
        para cada nodo viable. Luego normaliza y computa C_j.
        """
        raw: List[NodeCosts] = []

        for node in nodes:
            lat  = self._calc_latency(task, node)
            eng  = self._calc_energy(task, node)
            qpen = self._queue_penalty(node)
            lbal = self._load_balance_delta(node, nodes)
            raw.append(NodeCosts(node=node, latency=lat, energy_j=eng,
                                 queue_penalty=qpen, load_balance=lbal))

        if not raw:
            return raw

        # Normalización Min-Max 
        lats = [c.latency.total_ms for c in raw]
        engs = [c.energy_j for c in raw]
        qpens = [c.queue_penalty for c in raw]
        lbals = [c.load_balance for c in raw]

        def norm(v, mn, mx):
            return (v - mn) / (mx - mn) if mx - mn > EPSILON_TIE else 0.0

        # Coste Compuesto C_j
        for c in raw:
            nl = norm(c.latency.total_ms, min(lats), max(lats))
            ne = norm(c.energy_j, min(engs), max(engs))
            nq = norm(c.queue_penalty, min(qpens), max(qpens))
            nb = norm(c.load_balance, min(lbals), max(lbals))

            # Penalización extra por offloading a Cloud
            cloud_pen = self._p_cloud if c.node.node_type == NodeType.CLOUD else 0.0

            c.objectives = {
                "norm_latency": nl,
                "norm_energy": ne,
                "norm_queue": nq,
                "norm_load": nb,
            }
            c.composite = (
                self.beta  * nl +
                self.alpha * ne +
                0.15 * nq  +
                0.10 * nb  +
                cloud_pen
            )

        return raw

    # SELECCIÓN DE PARETO
    def _pareto_select(self, costs: List[NodeCosts]) -> Optional[NodeCosts]:
        """
        Selección basada en dominancia de Pareto entre los 2 mejores candidatos.
          - Si top1 domina top2 → selecciona top1
          - Si top2 domina top1 → selecciona top2
          - Empate → selecciona el de menor longitud de cola (e_tie)
        """
        if not costs:
            return None
        if len(costs) == 1:
            return costs[0]

        # Ordenar por coste compuesto C_j
        ranked = sorted(costs, key=lambda c: c.composite)
        top1, top2 = ranked[0], ranked[1]
        if self._dominates(top1, top2):
            logger.debug("Pareto: top1 domina a top2 → seleccionado %s",
                         top1.node.node_id[:8])
            return top1
        if self._dominates(top2, top1):
            logger.debug("Pareto: top2 domina a top1 → seleccionado %s",
                         top2.node.node_id[:8])
            return top2

        # Desempate por longitud de cola 
        selected = top1 if top1.node.queue_length <= top2.node.queue_length else top2
        logger.debug("Pareto: empate → cola menor → %s", selected.node.node_id[:8])
        return selected

    def _dominates(self, a: NodeCosts, b: NodeCosts) -> bool:
        """
        A domina a B si:
          - A no es inferior a B en ningún objetivo
          - A es estrictamente superior en al menos uno
        """
        objs = ["norm_latency", "norm_energy", "norm_queue", "norm_load"]

        not_worse = all(
            a.objectives[o] <= b.objectives[o] + EPSILON_TIE for o in objs
        )
        strictly_better = any(
            a.objectives[o] <  b.objectives[o] - EPSILON_TIE for o in objs
        )
        return not_worse and strictly_better

    #  CÁLCULO DE LATENCIA  t_total
    def _calc_latency(self, task: Task, node: NetworkNode) -> LatencyBreakdown:
        """
        t_total = t_uplink + t_downlink + t_comp + t_queue + t_ram_wait
        """
        # t_uplink: UE → gNB
        r_ul = self._shannon_rate(task.uplink_power_dbm, GNB_BANDWIDTH_MHZ * 1e6)
        tau_ul = (task.input_size_bits / r_ul) * 1e3  # s → ms

        # t_downlink: gNB → UE
        r_dl = self._shannon_rate(GNB_TX_POWER_DBM, GNB_BANDWIDTH_MHZ * 1e6)
        tau_dl = (task.output_size_bits / r_dl) * 1e3

        # t_comp: tiempo de procesamiento en el servidor
        cycles = task.input_size_bits * 500   # Ciclos estimados por bit
        tau_comp = (cycles / (node.cpu_ghz * 1e9)) * 1e3  # ms

        # t_queue: latencia de espera en cola
        tau_queue = node.queue_length * tau_comp

        # t_ram_wait: penalización si RAM > 80%
        util = node.utilization
        tau_ram = (util - 0.8) * 20.0 if util > 0.8 else 0.0

        # t_prop: retardo de propagación
        if node.node_type == NodeType.CLOUD:
            # Fibra óptica → 2×10^8 m/s 
            tau_prop = (CLOUD_FIBER_DISTANCE_M / FIBER_SPEED_MPS) * 1e3 * 2
        else:
            tau_prop = (node.distance_m / LIGHT_SPEED_MPS) * 1e3 * 2

        return LatencyBreakdown(
            t_uplink_ms = tau_ul,
            t_downlink_ms = tau_dl,
            t_comp_ms = tau_comp,
            tau_queue_ms = tau_queue,
            tau_ram_wait_ms = tau_ram,
            tau_prop_ms = tau_prop,
        )

    #  CÁLCULO DE ENERGÍA  E_total

    def _calc_energy(self, task: Task, node: NetworkNode) -> float:
        """
        E_total = E_trans + E_comp
        E_trans = E_{UE→gNB} + E_{gNB→Server}
        """
        # E_(UE→gNB): energía de transmisión uplink
        p_ue_w = self._dbm_to_mw(task.uplink_power_dbm) / 1000  # mW → W
        r_ul   = self._shannon_rate(task.uplink_power_dbm, GNB_BANDWIDTH_MHZ * 1e6)
        t_ul   = task.input_size_bits / r_ul
        e_ue_gnb = p_ue_w * t_ul

        # E_{gNB→Server}: energía de transmisión backhaul
        p_gnb_w = self._dbm_to_mw(GNB_TX_POWER_DBM) / 1000
        r_bh = self._shannon_rate(GNB_TX_POWER_DBM, node.bandwidth_mhz * 1e6)
        t_bh = task.input_size_bits / r_bh
        e_gnb_srv = p_gnb_w * t_bh
        e_trans = e_ue_gnb + e_gnb_srv

        # E_comp: energía de cómputo en el servidor
        cycles = task.input_size_bits * 500
        t_comp = cycles / (node.cpu_ghz * 1e9)
        e_comp = node.active_power_w * t_comp

        return e_trans + e_comp

    #  OBJETIVOS AUXILIARES
    def _queue_penalty(self, node: NetworkNode) -> float:
        """Penalización por saturación de cola (τ_queue management)."""
        return float(node.queue_length ** 2) * 0.05  # Cuadrático para mayor sensibilidad

    def _load_balance_delta(self, node: NetworkNode, all_nodes: List[NetworkNode]) -> float:
        """
        Equilibrio de Carga:
        Minimizar la desviación estándar del uso de RAM entre servidores
        Retorna el Δstd si se asigna una tarea a este nodo.
        """
        utils_before = [n.utilization for n in all_nodes]
        std_before = float(np.std(utils_before))

        # Por el momento se simula la asignación
        simulated = [
            (n.ram_used_gbit + 0.1) / n.ram_total_gbit
            if n.node_id == node.node_id else n.utilization
            for n in all_nodes
        ]
        std_after = float(np.std(simulated))

        return max(0.0, std_after - std_before)

    #  UTILITARIOS

    @staticmethod
    def _shannon_rate(tx_power_dbm: float, bandwidth_hz: float) -> float:
        """
        Capacidad de Shannon: R = B · log₂(1 + SINR)
        SINR calculado con piso de ruido -100 dBm.
        """
        tx_mw    = 10 ** (tx_power_dbm / 10)
        noise_mw = 10 ** (NOISE_FLOOR_DBM / 10)
        sinr     = tx_mw / noise_mw
        return bandwidth_hz * math.log2(1 + sinr)

    @staticmethod
    def _dbm_to_mw(dbm: float) -> float:
        return 10 ** (dbm / 10)

    @property
    def params(self) -> dict:
        return {
            "alpha": self.alpha,
            "beta": self.beta,
            "nu_threshold": self._nu_threshold,
            "p_cloud": self._p_cloud,
            "epsilon_tie": EPSILON_TIE,
        }

def GNB_MIMO_ANTENNAS_GAIN() -> float:
    """Ganancia lineal de las 64 antenas Massive MIMO del gNB."""
    from config import GNB_MIMO_ANTENNAS
    return GNB_MIMO_ANTENNAS  # Ganancia de arreglo = N antenas en lineal

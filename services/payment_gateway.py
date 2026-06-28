"""
Pasarela de Pagos y Módulo de Transacciones
Sección 5: Módulo de Transacciones y Pasarela de Pagos

Flujo de 5 pasos con validación de viabilidad MOTOA:
  1. Iniciación
  2. MOTOA Viability Check (RAM + recursos)
  3. Validación de Stock
  4. Procesamiento en Pasarela externa
  5. Cierre atómico: inventario + notificaciones
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from algorithms.motoa import MOTOAEngine
from config import NOISE_FLOOR_DBM
from models.node import NetworkNode
from models.product import Product
from models.task import OffloadingDecision, Task, TaskStatus
from models.user import UserProfile

logger = logging.getLogger(__name__)


class PaymentStatus(Enum):
    INITIATED       = "initiated"
    MOTOA_CHECK     = "motoa_viability_check"
    STOCK_CHECK     = "stock_validation"
    GATEWAY_PROCESS = "gateway_processing"
    COMPLETED       = "completed"
    FAILED          = "failed"
    INSUFFICIENT_RESOURCES = "insufficient_resources"


@dataclass
class PaymentRecord:
    transaction_id:  str
    user_id:         str
    product_id:      str
    amount:          float
    status:          PaymentStatus
    offloaded_to:    Optional[str]       = None
    server_id:       Optional[str]       = None
    created_at:      float               = field(default_factory=time.time)
    completed_at:    Optional[float]     = None
    error_message:   Optional[str]       = None
    audit_trail:     list                = field(default_factory=list)

    def log_step(self, step: str, detail: str = "") -> None:
        self.audit_trail.append({
            "ts":     time.time(),
            "step":   step,
            "detail": detail,
        })
        logger.debug("[TXN %s] %s – %s", self.transaction_id[:8], step, detail)

    def to_dict(self) -> dict:
        return {
            "transaction_id": self.transaction_id,
            "user_id":        self.user_id,
            "product_id":     self.product_id,
            "amount":         self.amount,
            "status":         self.status.value,
            "offloaded_to":   self.offloaded_to,
            "server_id":      self.server_id,
            "created_at":     self.created_at,
            "completed_at":   self.completed_at,
            "audit_trail":    self.audit_trail,
        }


@dataclass
class PaymentRequest:
    user_id:      str
    product_id:   str
    amount:       float
    payment_data: Dict[str, Any]   # Datos de pago (encriptados externamente)

    # Parámetros de red del UE al momento del pago
    uplink_power_dbm: float = 23.0   # Potencia uplink estándar UE (dBm)
    deadline_ms:      float = 5000.0 # Plazo de 5 s para la transacción


class PaymentGateway:
    """
    Pasarela de pagos integrada con verificación de recursos MOTOA.
    Garantiza integridad transaccional mediante actualización atómica
    de inventario y notificación dual (comprador + vendedor).
    """

    # Tamaño estimado de la tarea de pago (cifrado + lógica de validación)
    PAYMENT_TASK_INPUT_BITS:  float = 2.0e6   # 2 Mbit (datos cifrados)
    PAYMENT_TASK_OUTPUT_BITS: float = 0.5e6   # 0.5 Mbit (confirmación)

    def __init__(
        self,
        motoa_engine:     MOTOAEngine,
        notification_svc: Any,    # NotificationService (inyectado)
        products_store: Optional[Any] = None,
        users_store: Optional[Any] = None,
        payments_store: Optional[Any] = None,
    ):
        self.motoa      = motoa_engine
        self.notifier   = notification_svc
        self.products_store = products_store
        self.users_store    = users_store
        self.payments_store = payments_store
        self._records:  Dict[str, PaymentRecord] = {}

    # ──────────────────────────────────────────────────────────────────────────
    # Pipeline de Pago (5 pasos — Sección 5)
    # ──────────────────────────────────────────────────────────────────────────

    def process(
        self,
        request:         PaymentRequest,
        product:         Product,
        buyer:           UserProfile,
        nodes:           list,          # List[NetworkNode]
        global_queue:    int,
    ) -> PaymentRecord:
        """Ejecuta el flujo completo de pago."""

        txn = PaymentRecord(
            transaction_id = str(uuid.uuid4()),
            user_id        = request.user_id,
            product_id     = request.product_id,
            amount         = request.amount,
            status         = PaymentStatus.INITIATED,
        )
        self._records[txn.transaction_id] = txn
        if self.payments_store is not None:
            self.payments_store[txn.transaction_id] = txn
        txn.log_step("INICIADO", f"Usuario={request.user_id}, Monto=${request.amount}")

        try:
            # ── Paso 2: MOTOA Viability Check ─────────────────────────────────
            txn.status = PaymentStatus.MOTOA_CHECK
            if self.payments_store is not None:
                self.payments_store[txn.transaction_id] = txn
            server_id, decision = self._motoa_check(request, nodes, global_queue, txn)

            if txn.status == PaymentStatus.FAILED:
                return txn

            txn.offloaded_to = decision.value if decision else "local"
            txn.server_id    = server_id

            # ── Paso 3: Validación de Stock ───────────────────────────────────
            txn.status = PaymentStatus.STOCK_CHECK
            if self.payments_store is not None:
                self.payments_store[txn.transaction_id] = txn
            self._validate_stock(product, txn)

            if txn.status == PaymentStatus.FAILED:
                return txn

            # ── Paso 4: Procesamiento en Pasarela Externa ─────────────────────
            txn.status = PaymentStatus.GATEWAY_PROCESS
            if self.payments_store is not None:
                self.payments_store[txn.transaction_id] = txn
            self._process_gateway(request, txn)

            # ── Paso 5: Cierre Atómico ────────────────────────────────────────
            self._atomic_close(product, buyer, txn)

            txn.status       = PaymentStatus.COMPLETED
            txn.completed_at = time.time()
            txn.log_step("COMPLETADO", f"Stock restante={product.stock}")
            if self.payments_store is not None:
                self.payments_store[txn.transaction_id] = txn

            logger.info(
                "✔ Transacción %s completada | %s → %s (offload=%s)",
                txn.transaction_id[:8], buyer.name, product.name, txn.offloaded_to
            )

        except Exception as exc:
            txn.status        = PaymentStatus.FAILED
            txn.error_message = str(exc)
            txn.log_step("ERROR", str(exc))
            if self.payments_store is not None:
                self.payments_store[txn.transaction_id] = txn
            logger.exception("✘ Transacción %s fallida", txn.transaction_id[:8])

        return txn

    # ──────────────────────────────────────────────────────────────────────────
    # Implementación de cada paso
    # ──────────────────────────────────────────────────────────────────────────

    def _motoa_check(
        self,
        request:      PaymentRequest,
        nodes:        list,
        global_queue: int,
        txn:          PaymentRecord,
    ):
        """
        Paso 2: El gNB intercepta la tarea de transacción.
        Verifica que el servidor destino tenga RAM suficiente
        para el cifrado y la lógica de validación.
        """
        task = Task(
            user_id           = request.user_id,
            input_size_bits   = self.PAYMENT_TASK_INPUT_BITS,
            output_size_bits  = self.PAYMENT_TASK_OUTPUT_BITS,
            uplink_power_dbm  = request.uplink_power_dbm,
            deadline_ms       = request.deadline_ms,
        )

        server_id, decision = self.motoa.process_task(task, nodes, global_queue)

        if task.status == TaskStatus.DISCARDED:
            txn.status        = PaymentStatus.INSUFFICIENT_RESOURCES
            txn.error_message = "Recursos insuficientes o plazo vencido"
            txn.log_step("MOTOA_FAIL", "Tarea de pago descartada por MOTOA")
            txn.status        = PaymentStatus.FAILED
            return None, None

        txn.log_step(
            "MOTOA_OK",
            f"Asignado a {decision.value if decision else 'local'} | server={server_id}"
        )
        return server_id, decision

    def _validate_stock(self, product: Product, txn: PaymentRecord) -> None:
        """Paso 3: Consulta al Módulo del Emprendedor para disponibilidad inmediata."""
        if product.stock < 1:
            txn.status        = PaymentStatus.FAILED
            txn.error_message = f"Producto '{product.name}' sin stock"
            txn.log_step("STOCK_FAIL", f"stock={product.stock}")
            raise ValueError(txn.error_message)

        txn.log_step("STOCK_OK", f"Stock disponible={product.stock}")

    def _process_gateway(self, request: PaymentRequest, txn: PaymentRecord) -> None:
        """
        Paso 4: Offloading de la tarea a la Pasarela de Pago externa
        para validación bancaria (simulado).
        """
        # En producción: llamada real a Stripe / Culqi / MercadoPago etc.
        # Aquí simulamos la validación bancaria
        card_last4 = str(request.payment_data.get("card_last4", "****"))
        txn.log_step("GATEWAY_OK", f"Pago autorizado | tarjeta=****{card_last4}")

    def _atomic_close(
        self, product: Product, buyer: UserProfile, txn: PaymentRecord
    ) -> None:
        """
        Paso 5: Actualización atómica del inventario +
        notificación de éxito a comprador y vendedor.
        """
        # Actualización atómica de inventario
        product.stock -= 1
        buyer.record_transaction(txn.transaction_id)
        txn.log_step("INVENTARIO", f"Stock reducido → {product.stock}")
        if self.products_store is not None:
            self.products_store[product.product_id] = product
        if self.users_store is not None:
            self.users_store[buyer.user_id] = buyer

        # Notificaciones asíncronas
        if self.notifier:
            self.notifier.notify_payment_success(
                buyer_id       = buyer.user_id,
                entrepreneur_id= product.entrepreneur_id,
                product_name   = product.name,
                transaction_id = txn.transaction_id,
                amount         = txn.amount,
            )

    # ──────────────────────────────────────────────────────────────────────────

    def get_record(self, transaction_id: str) -> Optional[PaymentRecord]:
        return self._records.get(transaction_id)

    @property
    def all_records(self) -> list:
        return list(self._records.values())

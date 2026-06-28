"""
Servicio de notificaciones
Lógica de notificaciones

Envía alertas a compradores y vendedores tras eventos del sistema
Diseñado para ser reemplazado por push notifications en producción
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class NotificationType(Enum):
    PAYMENT_SUCCESS = "payment_success"
    PAYMENT_FAILED = "payment_failed"
    STOCK_LOW = "stock_low"
    TASK_COMPLETED = "task_completed"
    TASK_DISCARDED = "task_discarded"
    NEW_RECOMMENDATION = "new_recommendation"
    SYSTEM_ALERT = "system_alert"

@dataclass
class Notification:
    recipient_id: str
    notif_type: NotificationType
    title: str
    body: str
    payload: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    delivered: bool = False
    notif_id: str = field(
        default_factory=lambda: f"notif_{int(time.time()*1000)}"
    )

class NotificationService:
    """
    Servicio de notificaciones con soporte para múltiples canales.
    En producción: integrar con Firebase para notificaciones con Flutter
    """

    def __init__(self):
        self._inbox:    Dict[str, List[Notification]] = {}   # user_id → notifications
        self._handlers: Dict[NotificationType, List[Callable]] = {}

    # Registro de handlers

    def on(self, notif_type: NotificationType, handler: Callable) -> None:
        """Registra un handler para un tipo de notificación."""
        self._handlers.setdefault(notif_type, []).append(handler)

    # Envío 
    def send(self, recipient_id: str, notif_type: NotificationType,
             title: str, body: str, payload: dict = None) -> Notification:
        notif = Notification(
            recipient_id = recipient_id,
            notif_type = notif_type,
            title = title,
            body = body,
            payload = payload or {},
        )
        self._inbox.setdefault(recipient_id, []).append(notif)

        # Invocar handlers síncronos registrados
        for handler in self._handlers.get(notif_type, []):
            try:
                handler(notif)
            except Exception as exc:
                logger.warning("Handler error para %s: %s", notif_type.value, exc)

        notif.delivered = True
        logger.info(
            "Notificación → [%s] %s: %s",
            recipient_id[:8], notif_type.value, title
        )
        return notif

    # Eventos del sistema

    def notify_payment_success(
        self,
        buyer_id: str,
        entrepreneur_id: str,
        product_name: str,
        transaction_id: str,
        amount: float,
    ) -> None:
        """Notifica exito de pago al comprador y al emprendedor"""
        self.send(
            recipient_id = buyer_id,
            notif_type = NotificationType.PAYMENT_SUCCESS,
            title = "¡Compra exitosa!",
            body = f"Tu compra de '{product_name}' fue procesada correctamente.",
            payload = {"transaction_id": transaction_id, "amount": amount},
        )
        self.send(
            recipient_id = entrepreneur_id,
            notif_type = NotificationType.PAYMENT_SUCCESS,
            title = "Nueva venta",
            body = f"Vendiste '{product_name}' por ${amount:.2f}.",
            payload = {"transaction_id": transaction_id, "amount": amount},
        )

    def notify_task_discarded(self, user_id: str, reason: str) -> None:
        self.send(
            recipient_id = user_id,
            notif_type = NotificationType.TASK_DISCARDED,
            title = "Solicitud no procesada",
            body = f"Tu solicitud fue descartada: {reason}",
        )

    def notify_low_stock(self, entrepreneur_id: str, product_name: str, stock: int) -> None:
        self.send(
            recipient_id = entrepreneur_id,
            notif_type = NotificationType.STOCK_LOW,
            title = "Stock bajo",
            body = f"'{product_name}' tiene solo {stock} unidades.",
            payload = {"stock": stock},
        )

    def notify_recommendation(self, user_id: str, product_name: str) -> None:
        self.send(
            recipient_id = user_id,
            notif_type = NotificationType.NEW_RECOMMENDATION,
            title = "Producto recomendado para ti",
            body = f"¡Descubre '{product_name}' en UniMarket!",
        )

    #Consultas

    def get_inbox(self, user_id: str, unread_only: bool = False) -> List[Notification]:
        notifs = self._inbox.get(user_id, [])
        if unread_only:
            notifs = [n for n in notifs if not n.delivered]
        return sorted(notifs, key=lambda n: n.created_at, reverse=True)

    def mark_read(self, user_id: str, notif_id: str) -> bool:
        for n in self._inbox.get(user_id, []):
            if n.notif_id == notif_id:
                n.delivered = True
                return True
        return False

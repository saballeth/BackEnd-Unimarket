"""Persistencia mínima SQLite para el estado compartido de UniMarket"""

from __future__ import annotations
import json
import sqlite3
import threading
from collections.abc import MutableMapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, Generic, Iterator, Optional, TypeVar

T = TypeVar("T")

def default_db_path() -> Path:
    return Path(__file__).resolve().with_name("unimarket_state.sqlite3")

def _encode_value(value):
    if is_dataclass(value):
        return {k: _encode_value(v) for k, v in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _encode_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_encode_value(v) for v in value]
    return value

class SQLiteEntityStore(MutableMapping[str, T], Generic[T]):
    """Diccionario persistente respaldado por SQLite

    Mantiene la API de `dict` y expone `save()` para persistir mutaciones in-place.
    """

    def __init__(
        self,
        table_name: str,
        encode: Callable[[T], dict],
        decode: Callable[[dict], T],
        db_path: Optional[Path] = None,
    ):
        self.table_name = table_name
        self._encode = encode
        self._decode = decode
        self._db_path = db_path or default_db_path()
        self._lock = threading.RLock()
        self._data: Dict[str, T] = {}
        self._ensure_schema()
        self.reload()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    entity_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL DEFAULT (strftime('%s','now'))
                )
                """
            )
            conn.commit()

    def reload(self) -> None:
        with self._lock, self._connect() as conn:
            rows = conn.execute(f"SELECT entity_id, payload FROM {self.table_name}").fetchall()
            self._data = {
                row["entity_id"]: self._decode(json.loads(row["payload"]))
                for row in rows
            }

    def save(self, key: str, value: T) -> None:
        payload = json.dumps(_encode_value(self._encode(value)), ensure_ascii=True)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {self.table_name} (entity_id, payload, updated_at)
                VALUES (?, ?, strftime('%s','now'))
                ON CONFLICT(entity_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (key, payload),
            )
            conn.commit()
            self._data[key] = value

    def delete(self, key: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(f"DELETE FROM {self.table_name} WHERE entity_id = ?", (key,))
            conn.commit()
            self._data.pop(key, None)

    def __getitem__(self, key: str) -> T:
        return self._data[key]
    def __setitem__(self, key: str, value: T) -> None:
        self.save(key, value)
    def __delitem__(self, key: str) -> None:
        self.delete(key)
    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def product_to_payload(product) -> dict:
    return _encode_value(product)
def product_from_payload(payload: dict):
    from models.product import Product, ProductStatus
    data = dict(payload)
    data["status"] = ProductStatus(data["status"])
    return Product(**data)


def user_to_payload(user) -> dict:
    return _encode_value(user)


def user_from_payload(payload: dict):
    from models.user import UserProfile, UserRole

    data = dict(payload)
    data["role"] = UserRole(data["role"])
    return UserProfile(**data)


def task_to_payload(task) -> dict:
    return _encode_value(task)
def task_from_payload(payload: dict):
    from models.task import OffloadingDecision, Task, TaskStatus
    data = dict(payload)
    data["status"] = TaskStatus(data["status"])
    if data.get("offloading_decision") is not None:
        data["offloading_decision"] = OffloadingDecision(data["offloading_decision"])
    return Task(**data)

def payment_to_payload(record) -> dict:
    return _encode_value(record)
def payment_from_payload(payload: dict):
    from payment_gateway import PaymentRecord, PaymentStatus

    data = dict(payload)
    data["status"] = PaymentStatus(data["status"])
    return PaymentRecord(**data)

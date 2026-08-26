"""PostgreSQL-репозиторий документов/отчётов (JSONB).

Реализует тот же интерфейс, что ``JsonFileRepository`` (ADR-006): миграция
сводится к выбору бэкенда конфигурацией. Документы и отчёты хранятся
целиком как JSONB — модель данных не меняется, меняется только носитель.
"""

from __future__ import annotations

import threading
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class PostgresJsonRepository(Generic[T]):
    """JSONB-хранилище за интерфейсом репозитория.

    Соединение открывается на каждую операцию: при масштабе MVP это
    упрощает конкурентность и устойчивость (нет разделяемого состояния),
    накладные расходы несущественны.
    """

    def __init__(
        self,
        dsn: str,
        *,
        table: str,
        model: type[T],
        id_field: str,
    ) -> None:
        import psycopg

        self._psycopg = psycopg
        self._dsn = dsn
        self._table = table
        self._model = model
        self._id_field = id_field
        self._lock = threading.Lock()
        self._ensure_table()

    def _connect(self):
        return self._psycopg.connect(self._dsn)

    def _ensure_table(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._table} ("  # noqa: S608
                "id text PRIMARY KEY, data jsonb NOT NULL)"
            )

    def save(self, obj: T) -> T:
        obj_id = getattr(obj, self._id_field)
        payload = obj.model_dump_json()
        with self._lock, self._connect() as conn:
            conn.execute(
                f"INSERT INTO {self._table} (id, data) VALUES (%s, %s) "  # noqa: S608
                "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
                (obj_id, payload),
            )
        return obj

    def get(self, obj_id: str) -> T | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT data FROM {self._table} WHERE id = %s",  # noqa: S608
                (obj_id,),
            ).fetchone()
        if row is None:
            return None
        data = row[0]
        if isinstance(data, str):  # драйвер может вернуть строку
            import json

            data = json.loads(data)
        return self._model.model_validate(data)

    def list(self) -> list[T]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT data FROM {self._table} ORDER BY id"  # noqa: S608
            ).fetchall()
        import json

        items: list[T] = []
        for (data,) in rows:
            if isinstance(data, str):
                data = json.loads(data)
            items.append(self._model.model_validate(data))
        return items

    def delete(self, obj_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                f"DELETE FROM {self._table} WHERE id = %s",  # noqa: S608
                (obj_id,),
            )
            return (cursor.rowcount or 0) > 0

"""Структурированные логи стадий конвейера.

Принципы (docs/SECURITY.md п. 7): в логи не попадают тексты документов и
персональные данные — только идентификаторы запроса/документа, счётчики,
хэши и длительности стадий. Формат — одна строка key=value, пригодная для
grep/инжеста без дополнительной инфраструктуры.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger("second_opinion")


class StageTimer:
    """Замер длительности стадии с последующим структурированным логом."""

    def __init__(self) -> None:
        self._started = time.perf_counter()

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._started) * 1000)


def log_stage(
    request_id: str,
    stage: str,
    *,
    duration_ms: int | None = None,
    **fields: object,
) -> None:
    """Записать событие стадии.

    ``fields`` — только метаданные (счётчики, идентификаторы, версии).
    Значения приводятся к строке; многострочные значения запрещены
    форматированием, чтобы не ломать построчный парсинг логов.
    """
    parts = [f"stage={stage}", f"request_id={request_id}"]
    if duration_ms is not None:
        parts.append(f"duration_ms={duration_ms}")
    for key, value in fields.items():
        rendered = " ".join(str(value).split())
        parts.append(f"{key}={rendered}")
    logger.info(" ".join(parts))

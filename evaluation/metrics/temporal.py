"""Метрика временно́й корректности: выбор редакции нормы на дату.

Проверяет инвариант ADR-002: ``get_norm(norm_id, applicable_at)`` возвращает
именно ту редакцию, которая действовала в указанную дату, включая граничные
даты интервалов.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from second_opinion.legal_sources.store import NormStore


@dataclass(frozen=True)
class TemporalCase:
    """Ожидание: на дату ``applicable_at`` действует редакция ``expected_version_id``."""

    norm_id: str
    applicable_at: date
    expected_version_id: str


def temporal_accuracy(
    store: NormStore, cases: list[TemporalCase]
) -> tuple[float, list[TemporalCase]]:
    """Вернуть долю верно выбранных редакций и список проваленных кейсов."""
    if not cases:
        return 1.0, []
    failures: list[TemporalCase] = []
    for case in cases:
        version = store.get_norm(case.norm_id, case.applicable_at)
        if version.version_id != case.expected_version_id:
            failures.append(case)
    return (len(cases) - len(failures)) / len(cases), failures

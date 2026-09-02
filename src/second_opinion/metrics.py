"""Внутренние метрики конвейера (счётчики и простые гистограммы).

Назначение — эксплуатационная наблюдаемость без внешних зависимостей
(без ``prometheus_client``). Дизайн совместим с моделью метрик
Prometheus: счётчики и гистограммы; значения читаются через
``MetricsRegistry.snapshot()`` или ``/metrics`` эндпоинт (если
включён).

Включение: переменная окружения ``SO_METRICS_ENABLED=1`` (по
умолчанию выключено, чтобы не шуметь в логах и не тратить память,
когда метрики не нужны).
"""

from __future__ import annotations

import bisect
import os
import threading
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter

# Фиксированные границы бакетов для гистограмм (секунды).
# Покрывают типичный latency анализа: 1 мс — 1 с.
_LATENCY_BUCKETS: tuple[float, ...] = (
    0.001,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    float("inf"),
)


@dataclass
class _Counter:
    """Одноимённый счётчик с защитой от гонок."""

    name: str
    help_text: str
    value: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def inc(self, amount: int = 1) -> None:
        with self._lock:
            self.value += amount

    def get(self) -> int:
        with self._lock:
            return self.value


@dataclass
class _Histogram:
    """Гистограмма по фиксированным границам бакетов."""

    name: str
    help_text: str
    buckets: tuple[float, ...] = _LATENCY_BUCKETS
    _counts: list[int] = field(default_factory=lambda: [0] * len(_LATENCY_BUCKETS), repr=False)
    _sum: float = 0.0
    _count: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def observe(self, value: float) -> None:
        with self._lock:
            self._sum += value
            self._count += 1
            # bisect_right: первый бакет, верхняя граница которого > value
            index = bisect.bisect_left(self.buckets, value)
            if index >= len(self.buckets):
                index = len(self.buckets) - 1
            # +inf-бакет содержит всё
            self._counts[index] += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "buckets": list(self.buckets),
                "counts": list(self._counts),
                "sum": self._sum,
                "count": self._count,
            }


class MetricsRegistry:
    """Глобальный реестр метрик конвейера.

    Регистрация по имени: повторный ``counter()``/``histogram()`` с тем
    же именем возвращает уже существующий объект (идемпотентно).
    """

    def __init__(self, enabled: bool | None = None) -> None:
        self._enabled = (
            enabled if enabled is not None else os.environ.get("SO_METRICS_ENABLED") == "1"
        )
        self._counters: dict[str, _Counter] = {}
        self._histograms: dict[str, _Histogram] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        self._enabled = value

    def counter(self, name: str, help_text: str = "") -> _Counter:
        with self._lock:
            existing = self._counters.get(name)
            if existing is not None:
                return existing
            new = _Counter(name=name, help_text=help_text)
            self._counters[name] = new
            return new

    def histogram(self, name: str, help_text: str = "") -> _Histogram:
        with self._lock:
            existing = self._histograms.get(name)
            if existing is not None:
                return existing
            new = _Histogram(name=name, help_text=help_text)
            self._histograms[name] = new
            return new

    @contextmanager
    def time_histogram(self, name: str, help_text: str = "") -> object:
        """Контекст-менеджер: ``with registry.time_histogram(...):``.

        Гистограмма всегда регистрируется (для видимости в snapshot), но
        значения наблюдаются только если ``enabled=True``.
        """
        hist = self.histogram(name, help_text)
        if not self._enabled:
            yield
            return
        start = perf_counter()
        try:
            yield
        finally:
            hist.observe(perf_counter() - start)

    def snapshot(self) -> dict[str, object]:
        """Возвращает «дамп» всех метрик (счётчики и гистограммы)."""
        return {
            "counters": {name: c.get() for name, c in self._counters.items()},
            "histograms": {name: h.snapshot() for name, h in self._histograms.items()},
        }

    def reset(self) -> None:
        """Сбросить все метрики (для тестов)."""
        with self._lock:
            for c in self._counters.values():
                with c._lock:
                    c.value = 0
            for h in self._histograms.values():
                with h._lock:
                    h._counts = [0] * len(_LATENCY_BUCKETS)
                    h._sum = 0.0
                    h._count = 0


# Глобальный реестр — единственный, как AuditTrail.
_global_registry: MetricsRegistry | None = None
_global_lock = threading.Lock()


def get_metrics() -> MetricsRegistry:
    """Ленивая инициализация глобального реестра."""
    global _global_registry
    with _global_lock:
        if _global_registry is None:
            _global_registry = MetricsRegistry()
        return _global_registry


def reset_metrics_for_tests() -> None:
    """Сбросить глобальный реестр (используется в тестах)."""
    global _global_registry
    with _global_lock:
        if _global_registry is not None:
            _global_registry.reset()


# Имена метрик, которые используются в pipeline. Зафиксированы
# константами, чтобы случайно не разъехаться в разных местах.
ANALYSES_TOTAL = "second_opinion_analyses_total"
ANALYSES_FAILED_TOTAL = "second_opinion_analyses_failed_total"
FACTS_EXTRACTED = "second_opinion_facts_extracted_total"
RULE_EVALUATIONS = "second_opinion_rule_evaluations_total"
COMPARABLE_CASES = "second_opinion_comparable_cases_total"
ANALYZE_DURATION_SECONDS = "second_opinion_analyze_duration_seconds"
EXTRACT_DURATION_SECONDS = "second_opinion_extract_duration_seconds"
RULE_ENGINE_DURATION_SECONDS = "second_opinion_rule_engine_duration_seconds"
RETRIEVAL_DURATION_SECONDS = "second_opinion_retrieval_duration_seconds"

# Помощник для кратких текстов подсказок (для дампа).
METRIC_HELP: dict[str, str] = {
    ANALYSES_TOTAL: "Total number of successful analyses",
    ANALYSES_FAILED_TOTAL: "Total number of failed analyses",
    FACTS_EXTRACTED: "Total number of extracted legal facts",
    RULE_EVALUATIONS: "Total number of rule evaluations",
    COMPARABLE_CASES: "Total number of matched comparable cases",
    ANALYZE_DURATION_SECONDS: "End-to-end analyze duration (s)",
    EXTRACT_DURATION_SECONDS: "Fact extraction duration (s)",
    RULE_ENGINE_DURATION_SECONDS: "Rule engine duration (s)",
    RETRIEVAL_DURATION_SECONDS: "Case retrieval duration (s)",
}


def group_by_status(evaluations: list[object]) -> dict[str, int]:
    """Подсчёт статусов правил для одной оценки (для метрик)."""
    counts: dict[str, int] = defaultdict(int)
    for ev in evaluations:
        status = getattr(ev, "status", None)
        if status is not None:
            counts[str(getattr(status, "value", status))] += 1
    return dict(counts)

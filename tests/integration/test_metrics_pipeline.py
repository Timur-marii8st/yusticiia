"""Интеграционные тесты: метрики инкрементируются при analyze/update_fact."""

from __future__ import annotations

import pytest

from second_opinion.metrics import (
    ANALYSES_FAILED_TOTAL,
    ANALYSES_TOTAL,
    ANALYZE_DURATION_SECONDS,
    EXTRACT_DURATION_SECONDS,
    RETRIEVAL_DURATION_SECONDS,
    RULE_ENGINE_DURATION_SECONDS,
    RULE_EVALUATIONS,
    MetricsRegistry,
)
from tests.conftest import SAMPLE_CLEAN


def _install_metrics() -> MetricsRegistry:
    """Подменить глобальный реестр на свежий ``enabled=True``.

    Возвращает кортеж ``(registry, restore_fn)``: вызывающий код ОБЯЗАН
    вызвать ``restore_fn()`` в ``finally``.
    """
    import second_opinion.metrics as metrics_mod

    reg = MetricsRegistry(enabled=True)
    metrics_mod._global_registry = reg

    def _restore() -> None:
        metrics_mod._global_registry = None

    return reg, _restore


def test_analyze_increments_counters_and_histograms(pipeline) -> None:
    """Успешный analyze инкрементирует ANALYSES_TOTAL, гистограммы фаз,
    и счётчик RULE_EVALUATIONS на число правил движка (8: R-001–R-004,
    R-006, R-007, R-009, R-010)."""
    reg, restore = _install_metrics()
    try:
        # Счётчики, которые заведомо зарегистрированы после успешного analyze.
        before_total = reg.counter(ANALYSES_TOTAL).get()
        before_evals = reg.counter(RULE_EVALUATIONS).get()

        document = pipeline.ingest(SAMPLE_CLEAN.name, SAMPLE_CLEAN.read_bytes())
        report = pipeline.analyze(document.document_id)

        after = reg.snapshot()
        assert reg.counter(ANALYSES_TOTAL).get() == before_total + 1
        assert reg.counter(ANALYSES_FAILED_TOTAL).get() == 0
        assert reg.counter(RULE_EVALUATIONS).get() >= before_evals + 8
        # Гистограммы фаз зафиксировали по одному наблюдению.
        assert after["histograms"][ANALYZE_DURATION_SECONDS]["count"] == 1
        assert after["histograms"][EXTRACT_DURATION_SECONDS]["count"] == 1
        assert after["histograms"][RULE_ENGINE_DURATION_SECONDS]["count"] == 1
        assert after["histograms"][RETRIEVAL_DURATION_SECONDS]["count"] == 1
        assert after["histograms"][ANALYZE_DURATION_SECONDS]["sum"] > 0
        assert report.evaluations
    finally:
        restore()


def test_analyze_failure_increments_failed_counter(pipeline) -> None:
    """DocumentNotFound → ANALYSES_FAILED_TOTAL++, исключение пробрасывается."""
    from second_opinion.pipeline import DocumentNotFound

    reg, restore = _install_metrics()
    try:
        before_failed = reg.snapshot()["counters"].get(ANALYSES_FAILED_TOTAL, 0)

        with pytest.raises(DocumentNotFound):
            pipeline.analyze("no-such-document")

        after_failed = reg.snapshot()["counters"].get(ANALYSES_FAILED_TOTAL, 0)
        assert after_failed == before_failed + 1
    finally:
        restore()

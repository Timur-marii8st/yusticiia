"""Тесты модуля метрик (PipelineMetrics)."""

from __future__ import annotations

import time

import pytest

from second_opinion.metrics import (
    METRIC_HELP,
    MetricsRegistry,
    get_metrics,
    reset_metrics_for_tests,
)


def test_registry_disabled_does_not_observe_but_still_registers() -> None:
    """При выключенных метриках значения не наблюдаются, но гистограммы
    и счётчики регистрируются (видны в snapshot для инвентаризации)."""
    reg = MetricsRegistry(enabled=False)
    counter = reg.counter("x_total")
    assert counter.get() == 0
    with reg.time_histogram("x_dur"):
        time.sleep(0.001)
    snap = reg.snapshot()
    assert "x_total" in snap["counters"]
    assert snap["counters"]["x_total"] == 0
    assert "x_dur" in snap["histograms"]
    assert snap["histograms"]["x_dur"]["count"] == 0  # ничего не зафиксировано


def test_counter_and_histogram_observe_values() -> None:
    reg = MetricsRegistry(enabled=True)
    counter = reg.counter("requests_total")
    counter.inc()
    counter.inc(2)
    assert counter.get() == 3

    hist = reg.histogram("latency_seconds")
    hist.observe(0.01)
    hist.observe(0.5)
    hist.observe(2.0)
    snap = hist.snapshot()
    assert snap["count"] == 3
    assert snap["sum"] == pytest.approx(2.51)
    # bisect_left: 0.01 → бакет 0.025, 0.5 → бакет 1.0, 2.0 → бакет 2.5.
    # Значение попадает в бакет, верхняя граница которого ≥ value.
    assert snap["counts"][2] >= 1  # бакет 0.025
    assert snap["counts"][7] >= 1  # бакет 1.0
    assert snap["counts"][9] >= 1  # бакет 2.5 (2.0 < 2.5)
    # +inf-бакет ещё не задействован.
    assert snap["counts"][-1] == 0


def test_registry_idempotent_by_name() -> None:
    reg = MetricsRegistry(enabled=True)
    a = reg.counter("shared_total")
    b = reg.counter("shared_total")
    assert a is b
    a.inc()
    assert b.get() == 1


def test_time_histogram_measures_block() -> None:
    reg = MetricsRegistry(enabled=True)
    with reg.time_histogram("op_dur_seconds"):
        time.sleep(0.01)
    snap = reg.histogram("op_dur_seconds").snapshot()
    assert snap["count"] == 1
    assert snap["sum"] >= 0.01


def test_global_registry_resets_between_tests() -> None:
    reset_metrics_for_tests()
    reg = get_metrics()
    reg.counter("test_only_total").inc(5)
    assert reg.counter("test_only_total").get() == 5
    reset_metrics_for_tests()
    assert reg.counter("test_only_total").get() == 0


def test_metric_help_texts_present_for_named_metrics() -> None:
    """Имена, на которые ссылается pipeline, задокументированы."""
    expected = {
        "second_opinion_analyses_total",
        "second_opinion_analyses_failed_total",
        "second_opinion_facts_extracted_total",
        "second_opinion_rule_evaluations_total",
        "second_opinion_comparable_cases_total",
        "second_opinion_analyze_duration_seconds",
        "second_opinion_extract_duration_seconds",
        "second_opinion_rule_engine_duration_seconds",
        "second_opinion_retrieval_duration_seconds",
    }
    assert expected.issubset(METRIC_HELP.keys())


def test_snapshot_contains_all_known_names() -> None:
    reset_metrics_for_tests()
    reg = MetricsRegistry(enabled=True)
    for name in METRIC_HELP:
        if name.endswith("_total"):
            reg.counter(name).inc()
        else:
            reg.histogram(name).observe(0.001)
    snap = reg.snapshot()
    for name in METRIC_HELP:
        if name.endswith("_total"):
            assert name in snap["counters"], name
        else:
            assert name in snap["histograms"], name
    reset_metrics_for_tests()

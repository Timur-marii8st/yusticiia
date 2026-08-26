from __future__ import annotations

import logging

import pytest

from second_opinion.logging_utils import StageTimer, log_stage


def test_log_stage_emits_structured_line(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="second_opinion"):
        timer = StageTimer()
        log_stage(
            "req-123",
            "rule_engine",
            duration_ms=timer.elapsed_ms(),
            statuses={"PASS": 5},
        )
    record = caplog.records[-1]
    message = record.getMessage()
    assert "stage=rule_engine" in message
    assert "request_id=req-123" in message
    assert "duration_ms=" in message
    assert "statuses={'PASS': 5}" in message


@pytest.mark.parametrize("noisy", ["line1\nline2", "a  b"])
def test_log_stage_flattens_multiline_values(caplog, noisy: str) -> None:
    """Многострочные/лишние пробелы схлопываются: построчный парсинг логов
    не ломается; текст документа в логи не попадает по построению."""
    with caplog.at_level(logging.INFO, logger="second_opinion"):
        log_stage("req-1", "ingest", filename=noisy)
    message = caplog.records[-1].getMessage()
    assert "\n" not in message.split("filename=", 1)[1]

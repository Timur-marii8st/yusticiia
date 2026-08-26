from __future__ import annotations

import time

from tests.conftest import SAMPLE_CLEAN


def test_large_document_analysis_completes(pipeline) -> None:
    """Смок-тест производительности: документ ~200 тыс. символов (в пределах
    лимита загрузки) анализируется без деградации в патологических времена.
    Порог щедрый — тест ловит регрессии порядка величины (например,
    катастрофический бэктрекинг регулярных выражений), а не шум."""
    base = SAMPLE_CLEAN.read_text(encoding="utf-8")
    filler = (
        "\nДополнительные обстоятельства дела изложены в описательной части. "
        "Судом исследованы доказательства стороны защиты. "
    ) * 1500
    text = base + filler
    assert len(text) > 150_000

    document = pipeline.ingest("large.txt", text.encode("utf-8"))
    start = time.perf_counter()
    report = pipeline.analyze(document.document_id)
    elapsed = time.perf_counter() - start

    # содержательная корректность не зависит от размера
    assert report.facts
    statuses = {e.rule_id for e in report.evaluations}
    assert "R-001" in statuses
    assert elapsed < 15.0, f"анализ занял {elapsed:.1f} с"

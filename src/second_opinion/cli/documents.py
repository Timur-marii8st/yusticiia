"""Работа с документом из консоли: разбор, обезличивание, прогон метрик."""

from __future__ import annotations

import sys
from pathlib import Path

from ..config import load_config


def analyze(file_path: Path, applicable_at: str | None) -> int:
    from ..api.deps import build_pipeline

    pipeline = build_pipeline(load_config())
    content = file_path.read_bytes()
    document = pipeline.ingest(file_path.name, content)
    report = pipeline.analyze(document.document_id, applicable_at=applicable_at)

    print(f"Документ: {document.filename} ({len(document.text)} символов)")
    print(f"Юридически значимая дата: {report.applicable_at}"
          + (" (предположительно)" if report.applicable_at_assumed else ""))
    print(f"\nИзвлечено фактов: {len(report.facts)}")
    for fact in report.facts:
        evidence = fact.evidence[0].quote if fact.evidence else "—"
        print(f"  [{fact.status.value}] {fact.type.value}: {fact.value!r}  «{evidence[:60]}»")
    print(f"\nПроверок: {len(report.evaluations)}")
    for evaluation in report.evaluations:
        print(f"  [{evaluation.status.value}] {evaluation.headline}")
    print(f"\nСопоставимых дел: {len(report.comparable_cases)}")
    if report.analytics:
        print(f"  медиана срока: {report.analytics.median_months} мес. "
              f"(N={report.analytics.n_cases})")
    return 0
def anonymize(file_path: Path, output: Path | None) -> int:
    """Подготовить обезличенную копию документа.

    Нужна там, где реальное дело показывают посторонним: жюри,
    коллегам, в статье. Система при этом продолжает хранить оригинал —
    обезличивается то, что уходит наружу.
    """
    from ..ingestion.parser import ParseError, parse_document
    from ..privacy import detect_personal_data, mask_text

    try:
        document = parse_document(file_path.name, file_path.read_bytes(), 50_000_000)
    except (ParseError, OSError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2

    report = detect_personal_data(document.text)
    masked = mask_text(document.text, report)
    target = output or file_path.with_name(f"{file_path.stem}-обезличено.txt")
    target.write_text(masked, encoding="utf-8")

    print(f"Обнаружено фрагментов с персональными данными: {report.total}")
    for kind, count in sorted(report.by_kind().items()):
        print(f"  {kind}: {count}")
    print(f"Обезличенная копия: {target}")
    print(
        "Полнота обезличивания не гарантирована: пропущенное отчество или "
        "редкая фамилия останутся в тексте. Проверьте файл глазами."
    )
    return 0
def run_evaluation() -> int:
    from ..config import PROJECT_ROOT

    sys.path.insert(0, str(PROJECT_ROOT))
    from evaluation.run import main as eval_main

    return eval_main()

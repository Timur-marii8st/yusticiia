from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

from .config import load_config


def _serve(host: str, port: int) -> int:
    import uvicorn

    uvicorn.run(
        "second_opinion.api.app:make_app",
        factory=True,
        host=host,
        port=port,
        log_level="info",
    )
    return 0


def _analyze(file_path: Path, applicable_at: str | None) -> int:
    from .api.deps import build_pipeline

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


def _eval() -> int:
    from .config import PROJECT_ROOT

    sys.path.insert(0, str(PROJECT_ROOT))
    from evaluation.run import main as eval_main

    return eval_main()


def main(argv: list[str] | None = None) -> int:
    # Консоли с не-UTF-8 кодировкой не должны ронять вывод.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(ValueError, OSError):
                stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        prog="second-opinion",
        description="«Второе мнение»: юридический аудит проекта судебного акта",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="запуск веб-приложения")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8000)

    analyze_p = sub.add_parser("analyze", help="анализ файла в консоли")
    analyze_p.add_argument("file", type=Path)
    analyze_p.add_argument("--applicable-at", default=None, help="ISO-дата (ГГГГ-ММ-ДД)")

    sub.add_parser("eval", help="запуск evaluation-набора")

    args = parser.parse_args(argv)
    if args.command == "serve":
        return _serve(args.host, args.port)
    if args.command == "analyze":
        return _analyze(args.file, args.applicable_at)
    if args.command == "eval":
        return _eval()
    return 2


if __name__ == "__main__":
    sys.exit(main())

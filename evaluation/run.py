"""Запуск evaluation-набора: ``python -m evaluation.run`` или ``make eval``."""

from __future__ import annotations

import contextlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from evaluation.metrics.extraction import (  # noqa: E402
    evidence_correctness,
    precision_recall_f1,
    unsupported_claim_rate,
)
from second_opinion.domain.documents import Document  # noqa: E402
from second_opinion.fact_extraction.pattern_extractor import (  # noqa: E402
    PatternFactExtractor,
)


def main() -> int:
    # Консоли Windows могут иметь не-UTF-8 кодировку; отчёт не должен
    # теряться из-за этого.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(ValueError, OSError):
                stream.reconfigure(errors="replace")
    dataset_path = PROJECT_ROOT / "evaluation" / "datasets" / "golden_v1.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    extractor = PatternFactExtractor()

    results: list[dict] = []
    macro_f1: list[float] = []
    total_evidence_correct = 0
    total_evidence = 0
    unsupported_rates: list[float] = []

    for sample in dataset["samples"]:
        text = (PROJECT_ROOT / sample["path"]).read_text(encoding="utf-8")
        document = Document(
            document_id=sample["id"],
            filename=sample["path"],
            content_type="text/plain",
            text=text,
            sha256="",
        )
        facts = extractor.extract(document)
        predicted = {fact.type.value for fact in facts}
        gold = set(sample["expected_types"])
        precision, recall, f1 = precision_recall_f1(predicted, gold)
        correct, total = evidence_correctness(facts, document)
        rate = unsupported_claim_rate(facts)
        macro_f1.append(f1)
        total_evidence_correct += correct
        total_evidence += total
        unsupported_rates.append(rate)
        results.append(
            {
                "sample": sample["id"],
                "precision": round(precision, 3),
                "recall": round(recall, 3),
                "f1": round(f1, 3),
                "evidence_correct": correct,
                "evidence_total": total,
                "unsupported_claim_rate": rate,
                "missing": sorted(gold - predicted),
                "unexpected": sorted(predicted - gold),
            }
        )

    report = {
        "dataset_id": dataset["dataset_id"],
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "macro_f1": round(sum(macro_f1) / len(macro_f1), 3) if macro_f1 else None,
        "evidence_correctness": (
            round(total_evidence_correct / total_evidence, 3) if total_evidence else None
        ),
        "unsupported_claim_rate_max": max(unsupported_rates) if unsupported_rates else None,
        "samples": results,
    }

    reports_dir = PROJECT_ROOT / "evaluation" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = reports_dir / f"eval_{stamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nОтчёт сохранён: {report_path}")

    if (report["macro_f1"] or 0) < 0.8:
        print("ВНИМАНИЕ: макросредний F1 ниже порога 0.8", file=sys.stderr)
        return 1
    if (report["unsupported_claim_rate_max"] or 0) > 0:
        print("ВНИМАНИЕ: обнаружены подтверждённые факты без доказательств", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

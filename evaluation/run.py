"""Запуск evaluation-набора: ``python -m evaluation.run`` или ``make eval``.

Три раздела:
- extraction — качество извлечения фактов (P/R/F1, evidence, unsupported);
- retrieval  — качество поиска по базе источников (Recall@K, MRR, nDCG,
  целостность цитирования);
- temporal   — правильно ли выбирается редакция нормы на дату.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from evaluation.metrics.extraction import (  # noqa: E402
    evidence_correctness,
    precision_recall_f1,
    unsupported_claim_rate,
)
from evaluation.metrics.retrieval import mrr, ndcg_at_k, recall_at_k  # noqa: E402
from evaluation.metrics.temporal import TemporalCase, temporal_accuracy  # noqa: E402
from second_opinion.config import load_config  # noqa: E402
from second_opinion.domain.documents import Document  # noqa: E402
from second_opinion.fact_extraction.pattern_extractor import (  # noqa: E402
    PatternFactExtractor,
)
from second_opinion.legal_rag import LegalRag  # noqa: E402
from second_opinion.legal_sources.store import NormStore  # noqa: E402

#: Пороги приёмки; нарушение любого из них даёт ненулевой код возврата.
EXTRACTION_F1_THRESHOLD = 0.8


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _run_extraction(dataset_path: Path) -> dict:
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

    return {
        "dataset_id": dataset["dataset_id"],
        "macro_f1": _mean(macro_f1),
        "evidence_correctness": (
            round(total_evidence_correct / total_evidence, 3) if total_evidence else None
        ),
        "unsupported_claim_rate_max": max(unsupported_rates) if unsupported_rates else None,
        "samples": results,
    }


def _run_retrieval(dataset_path: Path) -> dict:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    store = NormStore.from_directory(load_config().fixtures_dir / "norms")
    rag = LegalRag(store)

    results: list[dict] = []
    recalls: list[float] = []
    rrs: list[float] = []
    ndcgs: list[float] = []
    citation_checked = 0
    citation_ok = 0

    for case in dataset["queries"]:
        hits = rag.search(case["query"], limit=10)
        ranked_ids = [hit.norm_id for hit in hits]
        relevant = set(case.get("relevant", []))
        expect_empty = bool(case.get("expect_empty"))

        entry: dict = {"query_id": case["id"], "top_norm_ids": ranked_ids[:5]}

        if expect_empty:
            entry["honest_empty"] = not ranked_ids
            recalls.append(1.0 if not ranked_ids else 0.0)
            results.append(entry)
            continue

        recall = recall_at_k(ranked_ids, relevant, k=5)
        rr = mrr(ranked_ids, relevant)
        ndcg = ndcg_at_k(ranked_ids, relevant, k=10)
        recalls.append(recall)
        rrs.append(rr)
        ndcgs.append(ndcg)
        entry.update(
            {
                "relevant": sorted(relevant),
                "recall_at_5": round(recall, 3),
                "mrr": round(rr, 3),
                "ndcg_at_10": round(ndcg, 3),
            }
        )
        results.append(entry)

        # Целостность цитирования: каждый фрагмент обязан совпадать с текстом
        # реально хранящейся редакции, а контрольная сумма — с пересчитанной.
        for hit in hits:
            citation_checked += 1
            version = next(
                (
                    v
                    for v in store.versions(hit.norm_id)
                    if v.version_id == hit.version_id
                ),
                None,
            )
            if version is None:
                continue
            sha_matches = version.sha256 == hashlib.sha256(
                version.text.encode("utf-8")
            ).hexdigest()
            if hit.fragment == version.text and sha_matches:
                citation_ok += 1

    return {
        "dataset_id": dataset["dataset_id"],
        "queries_total": len(dataset["queries"]),
        "recall_at_5_mean": _mean(recalls),
        "mrr_mean": _mean(rrs),
        "ndcg_at_10_mean": _mean(ndcgs),
        "citation_integrity": round(citation_ok / citation_checked, 3)
        if citation_checked
        else None,
        "queries": results,
    }


def _run_temporal(dataset_path: Path) -> dict:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    store = NormStore.from_directory(load_config().fixtures_dir / "norms")
    cases = [
        TemporalCase(
            norm_id=item["norm_id"],
            applicable_at=date.fromisoformat(item["applicable_at"]),
            expected_version_id=item["expected_version_id"],
        )
        for item in dataset["temporal_cases"]
    ]
    accuracy, failures = temporal_accuracy(store, cases)
    return {
        "dataset_id": dataset["dataset_id"],
        "cases_total": len(cases),
        "accuracy": round(accuracy, 3),
        "failures": [
            {
                "norm_id": case.norm_id,
                "applicable_at": case.applicable_at.isoformat(),
                "expected_version_id": case.expected_version_id,
            }
            for case in failures
        ],
    }


def main() -> int:
    # Консоли Windows могут иметь не-UTF-8 кодировку; отчёт не должен
    # теряться из-за этого.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(ValueError, OSError):
                stream.reconfigure(errors="replace")

    datasets_dir = PROJECT_ROOT / "evaluation" / "datasets"
    extraction = _run_extraction(datasets_dir / "golden_v1.json")
    retrieval = _run_retrieval(datasets_dir / "retrieval_v1.json")
    temporal = _run_temporal(datasets_dir / "retrieval_v1.json")

    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "extraction": extraction,
        "retrieval": retrieval,
        "temporal": temporal,
    }

    reports_dir = PROJECT_ROOT / "evaluation" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = reports_dir / f"eval_{stamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nОтчёт сохранён: {report_path}")

    exit_code = 0
    if (extraction["macro_f1"] or 0) < EXTRACTION_F1_THRESHOLD:
        print("ВНИМАНИЕ: макросредний F1 извлечения ниже порога "
              f"{EXTRACTION_F1_THRESHOLD}", file=sys.stderr)
        exit_code = 1
    if (extraction["unsupported_claim_rate_max"] or 0) > 0:
        print("ВНИМАНИЕ: обнаружены подтверждённые факты без доказательств",
              file=sys.stderr)
        exit_code = 1
    if (retrieval["recall_at_5_mean"] or 0) < 0.8:
        print("ВНИМАНИЕ: средний Recall@5 поиска ниже порога 0.8", file=sys.stderr)
        exit_code = 1
    if (retrieval["citation_integrity"] or 0) < 1.0:
        print("ВНИМАНИЕ: нарушена целостность цитирования источников",
              file=sys.stderr)
        exit_code = 1
    if (temporal["accuracy"] or 0) < 1.0:
        print("ВНИМАНИЕ: ошибки выбора редакции нормы на дату", file=sys.stderr)
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

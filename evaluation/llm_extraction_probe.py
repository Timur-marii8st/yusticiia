"""Замер извлечения языковой моделью на золотом наборе — `python -m evaluation.llm_extraction_probe`.

Отвечает на один вопрос: сколько добавляет модель поверх паттернового
извлекателя, если её включить в конвейер так же, как это сделал бы
оператор (``SO_LLM_PROVIDER=openai_compatible``, маскирование ПДн
включено по умолчанию). Использует ровно те классы, что работают в
проде — ``PatternFactExtractor``, ``LLMFactExtractor``,
``OpenAICompatibleProvider`` — а не отдельный тестовый путь, иначе замер
измерял бы не то, что реально запускается.

Требует поднятого OpenAI-совместимого контура (локально — Ollama/vLLM,
см. .env.example). Без него падает с понятным сообщением, а не тихо
использует мок: замер без настоящей модели не имеет смысла.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from evaluation.metrics.extraction import precision_recall_f1  # noqa: E402
from second_opinion.audit.trail import AuditTrail  # noqa: E402
from second_opinion.config import load_config  # noqa: E402
from second_opinion.domain.documents import Document  # noqa: E402
from second_opinion.fact_extraction.llm_extractor import LLMFactExtractor  # noqa: E402
from second_opinion.fact_extraction.pattern_extractor import (  # noqa: E402
    PatternFactExtractor,
    _value_key,
)
from second_opinion.llm.openai_compatible import OpenAICompatibleProvider  # noqa: E402

DATASET = PROJECT_ROOT / "evaluation" / "datasets" / "golden_v1.json"


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def main() -> int:
    config = load_config()
    if config.llm_provider != "openai_compatible" or not config.openai_base_url:
        print(
            "SO_LLM_PROVIDER=openai_compatible и SO_OPENAI_BASE_URL не заданы — "
            "замеру нечего измерять без настоящей модели. Пример для Ollama:\n"
            "  SO_LLM_PROVIDER=openai_compatible\n"
            "  SO_OPENAI_BASE_URL=http://127.0.0.1:11434/v1\n"
            "  SO_OPENAI_API_KEY=local\n"
            "  SO_LLM_MODEL=qwen2.5:7b-instruct",
            file=sys.stderr,
        )
        return 2

    provider = OpenAICompatibleProvider(
        config.openai_base_url,
        config.openai_api_key,
        config.llm_model,
        auth_scheme=config.llm_auth_scheme,
        timeout=120.0,
    )
    pattern_extractor = PatternFactExtractor()
    llm_extractor = LLMFactExtractor(provider, audit=AuditTrail(config.audit_path))

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))

    pattern_only_f1: list[float] = []
    combined_f1: list[float] = []
    latencies: list[float] = []
    rows: list[dict] = []

    for sample in dataset["samples"]:
        text = (PROJECT_ROOT / sample["path"]).read_text(encoding="utf-8")
        document = Document(
            document_id=sample["id"],
            filename=sample["path"],
            content_type="text/plain",
            text=text,
            sha256="",
        )
        gold = set(sample["expected_types"])

        pattern_facts = pattern_extractor.extract(document)
        known_keys = {(f.type.value, _value_key(f.value)) for f in pattern_facts}
        pattern_types = {f.type.value for f in pattern_facts}
        _, _, f1_pattern = precision_recall_f1(pattern_types, gold)
        pattern_only_f1.append(f1_pattern)

        started = time.monotonic()
        llm_facts = llm_extractor.extract(document, set(known_keys))
        elapsed = time.monotonic() - started
        latencies.append(elapsed)

        combined_types = pattern_types | {f.type.value for f in llm_facts}
        _, _, f1_combined = precision_recall_f1(combined_types, gold)
        combined_f1.append(f1_combined)

        row = {
            "sample": sample["id"],
            "pattern_f1": round(f1_pattern, 3),
            "combined_f1": round(f1_combined, 3),
            "delta": round(f1_combined - f1_pattern, 3),
            "llm_facts_accepted": len(llm_facts),
            "llm_added_types": sorted({f.type.value for f in llm_facts} - pattern_types),
            "latency_s": round(elapsed, 2),
        }
        rows.append(row)
        print(
            f"{sample['id']:32s} паттерны F1={f1_pattern:.3f}  "
            f"+модель F1={f1_combined:.3f}  "
            f"({elapsed:.1f} с, добавлено {len(llm_facts)})"
        )

    report = {
        "dataset_id": dataset["dataset_id"],
        "provider": provider.name,
        "model": config.llm_model,
        "pattern_only_macro_f1": _mean(pattern_only_f1),
        "combined_macro_f1": _mean(combined_f1),
        "delta_macro_f1": round(_mean(combined_f1) - _mean(pattern_only_f1), 3),
        "mean_latency_s": _mean(latencies),
        "max_latency_s": round(max(latencies), 2) if latencies else 0.0,
        "samples": rows,
    }

    print()
    print(f"паттерны:      macro-F1 = {report['pattern_only_macro_f1']}")
    print(f"паттерны+модель: macro-F1 = {report['combined_macro_f1']}")
    print(f"прирост:       {report['delta_macro_f1']:+.3f}")
    print(
        f"задержка:      среднее {report['mean_latency_s']} с, "
        f"максимум {report['max_latency_s']} с"
    )

    out_dir = PROJECT_ROOT / "evaluation" / "reports"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"llm_extraction_probe_{config.llm_model.replace('/', '_')}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nОтчёт сохранён: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

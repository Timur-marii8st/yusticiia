"""Метрики качества извлечения фактов.

Отдельно измеряются:
- точность/полнота по типам фактов (precision / recall / F1);
- корректность и покрытие доказательств (evidence correctness / coverage).
"""

from __future__ import annotations

from second_opinion.domain.documents import Document
from second_opinion.domain.enums import FactStatus
from second_opinion.domain.facts import LegalFact


def precision_recall_f1(
    predicted: set[str], gold: set[str]
) -> tuple[float, float, float]:
    if not predicted and not gold:
        return 1.0, 1.0, 1.0
    true_positive = len(predicted & gold)
    precision = true_positive / len(predicted) if predicted else 0.0
    recall = true_positive / len(gold) if gold else 0.0
    if precision + recall == 0:
        return precision, recall, 0.0
    f1 = 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def evidence_correctness(facts: list[LegalFact], document: Document) -> tuple[int, int]:
    """Сколько доказательств действительно присутствуют в тексте по оффсетам."""
    correct = 0
    total = 0
    for fact in facts:
        for evidence in fact.evidence:
            total += 1
            fragment = document.text[evidence.start_offset:evidence.end_offset]
            if _normalize(fragment) == _normalize(evidence.quote):
                correct += 1
    return correct, total


def unsupported_claim_rate(facts: list[LegalFact]) -> float:
    """Доля подтверждённых (VERIFIED) фактов без доказательств. Цель: 0."""
    verified = [f for f in facts if f.status is FactStatus.VERIFIED]
    if not verified:
        return 0.0
    unsupported = [f for f in verified if not f.evidence]
    return len(unsupported) / len(verified)


def _normalize(text: str) -> str:
    return " ".join(text.split()).lower()

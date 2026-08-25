"""Метрики качества поиска по базе источников (Legal RAG).

Все функции принимают ранжированный список идентификаторов (первый элемент —
лучший) и множество релевантных идентификаторов. Релевантность бинарная:
норма либо является ожидаемым ответом, либо нет.

- Recall@K — доля релевантных норм, попавших в первые K позиций;
- MRR — среднее обратное ранга первой релевантной позиции;
- nDCG@K — нормализованный дисконтированный совокупный выигрыш.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def recall_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    """Доля релевантных документов в первых ``k`` позициях ранжирования."""
    if not relevant:
        return 1.0
    top = set(ranked[:k])
    return len(top & relevant) / len(relevant)


def mrr(ranked: Sequence[str], relevant: set[str]) -> float:
    """Обратный ранг первой релевантной позиции; 0, если релевантных нет."""
    for position, doc_id in enumerate(ranked, start=1):
        if doc_id in relevant:
            return 1.0 / position
    return 0.0


def ndcg_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    """nDCG@K с бинарной релевантностью."""
    if not relevant:
        return 1.0
    dcg = sum(
        1.0 / math.log2(position + 1)
        for position, doc_id in enumerate(ranked[:k], start=1)
        if doc_id in relevant
    )
    ideal = sum(1.0 / math.log2(position + 1) for position in range(1, min(k, len(relevant)) + 1))
    if ideal == 0:
        return 0.0
    return dcg / ideal

"""Положение назначенного срока в распределении по практике (ФТ-27).

Требование Положения называется «выявление статистических отклонений»,
но выборка синтетическая, малая и неслучайная, а назначение наказания
зависит от обстоятельств, которых в сравнении нет. Поэтому проверяется
не только арифметика, но и то, что формулировки остаются описательными:
статистика не может служить основанием для наказания
(инвариант И-07 в docs/TZ.md).
"""

from __future__ import annotations

from datetime import date

from second_opinion.analytics.descriptive import compute_analytics
from second_opinion.domain.cases import ComparableCase


def _case(case_id: str, term: float) -> ComparableCase:
    return ComparableCase(
        case_id=case_id,
        title=f"Синтетическое дело {case_id}",
        court="Н-ский районный суд",
        date=date(2025, 3, 15),
        article=158,
        part=2,
        stage="completed",
        punishment_type="imprisonment",
        term_months=term,
        suspended=False,
        synthetic=True,
    )


CASES = [_case(str(i), term) for i, term in enumerate([12, 18, 24, 30, 36], start=1)]


def test_position_is_none_without_a_term() -> None:
    """Не с чем сравнивать — нет и наблюдения, а не «ноль отклонения»."""
    assert compute_analytics(CASES).sentence_position is None


def test_position_is_none_without_comparable_terms() -> None:
    assert compute_analytics([], 24).sentence_position is None


def test_term_in_the_middle_is_typical() -> None:
    position = compute_analytics(CASES, 24).sentence_position
    assert position is not None
    assert position.band == "typical"
    assert position.n_compared == 5
    assert position.share_at_or_below == 0.6


def test_term_below_the_lower_quarter() -> None:
    position = compute_analytics(CASES, 13).sentence_position
    assert position.band == "below"


def test_term_above_the_upper_quarter() -> None:
    position = compute_analytics(CASES, 34).sentence_position
    assert position.band == "above"


def test_term_outside_the_observed_range() -> None:
    position = compute_analytics(CASES, 60).sentence_position
    assert position.band == "outside"
    assert "вне диапазона" in position.note


def test_wording_stays_descriptive() -> None:
    """Из наблюдения нельзя вычитать рекомендацию о наказании."""
    for term in (6, 24, 60):
        note = compute_analytics(CASES, term).sentence_position.note.lower()
        for forbidden in ("следует", "рекомендуется", "должен", "завышен", "занижен"):
            assert forbidden not in note, f"предписывающая формулировка: {note}"

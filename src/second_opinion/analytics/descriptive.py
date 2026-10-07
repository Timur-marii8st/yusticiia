from __future__ import annotations

import statistics

from ..domain.analysis import AnalyticsSummary, SentencePosition
from ..domain.cases import ComparableCase
from ..domain.enums import PunishmentType

SPREAD_FEATURES = (
    "special_procedure",
    "guilty_plea",
    "recidivism",
    "jury_trial",
    "group",
    "suspended",
)


def _position(
    term: float, sorted_terms: list[float], p25: float, p75: float
) -> SentencePosition:
    """Положение назначенного срока в ряду сроков по сопоставимым делам.

    Намеренно не называется «отклонением от нормы»: нормы здесь нет.
    Есть ряд чисел из неслучайной выборки и место назначенного срока в
    нём. Формулировки подобраны так, чтобы из них нельзя было вычитать
    рекомендацию по наказанию — переименование в термины вида
    «завышен/занижен» ловится тестом.
    """
    at_or_below = sum(1 for value in sorted_terms if value <= term)
    share = round(at_or_below / len(sorted_terms), 3)

    if term < sorted_terms[0] or term > sorted_terms[-1]:
        band = "outside"
        note = (
            f"Срок {term:g} мес. вне диапазона сопоставимых дел "
            f"({sorted_terms[0]:g}–{sorted_terms[-1]:g} мес.). "
            "Это описательное наблюдение по малой неслучайной выборке, "
            "а не признак ошибки: обстоятельства дела могут объяснять разницу."
        )
    elif term < p25:
        band = "below"
        note = (
            f"Срок {term:g} мес. ниже нижней четверти выборки ({p25:g} мес.). "
            "Наблюдение описательное."
        )
    elif term > p75:
        band = "above"
        note = (
            f"Срок {term:g} мес. выше верхней четверти выборки ({p75:g} мес.). "
            "Наблюдение описательное."
        )
    else:
        band = "typical"
        note = (
            f"Срок {term:g} мес. в средней половине выборки "
            f"({p25:g}–{p75:g} мес.)."
        )

    return SentencePosition(
        term_months=term,
        n_compared=len(sorted_terms),
        band=band,
        share_at_or_below=share,
        note=note,
    )


def compute_analytics(
    cases: list[ComparableCase], term_months: float | None = None
) -> AnalyticsSummary:
    """Описательная статистика по выборке сопоставимых дел.

    Статистика носит ТОЛЬКО описательный характер и не является
    основанием или рекомендацией по наказанию.

    ``term_months`` — срок из проверяемого проекта акта. Если он передан
    и в выборке есть с чем сравнивать, считается положение срока в
    распределении (ФТ-27).
    """
    terms = [
        float(case.term_months)
        for case in cases
        if case.term_months is not None
        and case.punishment_type == PunishmentType.IMPRISONMENT.value
    ]
    distribution: dict[str, int] = {}
    for case in cases:
        distribution[case.punishment_type] = distribution.get(case.punishment_type, 0) + 1
    suspended_count = sum(1 for case in cases if case.suspended)
    feature_spread = {
        feature: round(
            sum(1 for case in cases if getattr(case, feature)) / len(cases), 3
        )
        for feature in SPREAD_FEATURES
    } if cases else {}

    def percentile(sorted_terms: list[float], fraction: float) -> float:
        if not sorted_terms:
            raise ValueError
        if len(sorted_terms) == 1:
            return sorted_terms[0]
        rank = fraction * (len(sorted_terms) - 1)
        lower_index = int(rank)
        upper_index = min(lower_index + 1, len(sorted_terms) - 1)
        weight = rank - lower_index
        return sorted_terms[lower_index] * (1 - weight) + sorted_terms[upper_index] * weight

    sorted_terms = sorted(terms)
    p25 = percentile(sorted_terms, 0.25) if terms else None
    p75 = percentile(sorted_terms, 0.75) if terms else None
    position = (
        _position(float(term_months), sorted_terms, p25, p75)
        if term_months is not None and terms and p25 is not None and p75 is not None
        else None
    )
    return AnalyticsSummary(
        n_cases=len(cases),
        n_with_term=len(terms),
        median_months=statistics.median(terms) if terms else None,
        mean_months=round(statistics.fmean(terms), 2) if terms else None,
        p25_months=p25,
        p75_months=p75,
        min_months=sorted_terms[0] if terms else None,
        max_months=sorted_terms[-1] if terms else None,
        punishment_type_distribution=distribution,
        suspended_share=round(suspended_count / len(cases), 3) if cases else None,
        feature_spread=feature_spread,
        sentence_position=position,
    )

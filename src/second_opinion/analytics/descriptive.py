from __future__ import annotations

import statistics

from ..domain.analysis import AnalyticsSummary
from ..domain.cases import ComparableCase
from ..domain.enums import PunishmentType


def compute_analytics(cases: list[ComparableCase]) -> AnalyticsSummary:
    """Описательная статистика по выборке сопоставимых дел.

    Статистика носит ТОЛЬКО описательный характер и не является
    основанием или рекомендацией по наказанию.
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
    return AnalyticsSummary(
        n_cases=len(cases),
        n_with_term=len(terms),
        median_months=statistics.median(terms) if terms else None,
        mean_months=round(statistics.fmean(terms), 2) if terms else None,
        p25_months=percentile(sorted_terms, 0.25) if terms else None,
        p75_months=percentile(sorted_terms, 0.75) if terms else None,
        min_months=sorted_terms[0] if terms else None,
        max_months=sorted_terms[-1] if terms else None,
        punishment_type_distribution=distribution,
        suspended_share=round(suspended_count / len(cases), 3) if cases else None,
    )

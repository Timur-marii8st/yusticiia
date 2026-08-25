from __future__ import annotations

from second_opinion.analytics.descriptive import compute_analytics
from second_opinion.domain.cases import ComparableCase


def _case(term: float | None, suspended: bool = False, ptype: str = "imprisonment") -> ComparableCase:
    return ComparableCase(
        case_id=f"c-{term}",
        title="т",
        court="суд",
        date="2024-01-01",
        article=158,
        part=2,
        punishment_type=ptype,
        term_months=term,
        suspended=suspended,
    )


def test_descriptive_stats_on_known_distribution() -> None:
    cases = [_case(12), _case(18), _case(24), _case(36, suspended=True)]
    analytics = compute_analytics(cases)
    assert analytics.n_cases == 4
    assert analytics.n_with_term == 4
    assert analytics.median_months == 21.0
    assert analytics.min_months == 12
    assert analytics.max_months == 36
    assert analytics.p25_months == 16.5
    assert analytics.p75_months == 27.0
    assert analytics.suspended_share == 0.25
    assert analytics.feature_spread["suspended"] == 0.25
    assert analytics.feature_spread["recidivism"] == 0.0


def test_feature_spread_counts_flags() -> None:
    cases = [
        _case(12, suspended=True),
        _case(24),
    ]
    cases[0].special_procedure = True
    cases[0].guilty_plea = True
    cases[1].recidivism = True
    analytics = compute_analytics(cases)
    assert analytics.feature_spread["special_procedure"] == 0.5
    assert analytics.feature_spread["guilty_plea"] == 0.5
    assert analytics.feature_spread["recidivism"] == 0.5
    assert analytics.feature_spread["jury_trial"] == 0.0


def test_non_imprisonment_terms_excluded_from_term_stats() -> None:
    cases = [_case(12), _case(10, ptype="correctional_labor")]
    analytics = compute_analytics(cases)
    assert analytics.n_with_term == 1
    assert analytics.median_months == 12


def test_empty_sample() -> None:
    analytics = compute_analytics([])
    assert analytics.n_cases == 0
    assert analytics.median_months is None
    assert analytics.suspended_share is None

from __future__ import annotations

from datetime import date

from evaluation.metrics.retrieval import mrr, ndcg_at_k, recall_at_k
from evaluation.metrics.temporal import TemporalCase, temporal_accuracy

# -- Recall@K ------------------------------------------------------------------


def test_recall_all_relevant_in_top_k() -> None:
    assert recall_at_k(["a", "b", "c"], {"a", "c"}, k=3) == 1.0


def test_recall_partial() -> None:
    assert recall_at_k(["a", "x", "y"], {"a", "b"}, k=5) == 0.5


def test_recall_nothing_relevant_found() -> None:
    assert recall_at_k(["x", "y"], {"a"}, k=3) == 0.0


def test_recall_empty_relevant_is_vacuously_one() -> None:
    assert recall_at_k(["x"], set(), k=3) == 1.0


def test_recall_respects_cutoff() -> None:
    # Релевантный документ есть, но за пределами k.
    assert recall_at_k(["x", "y", "a"], {"a"}, k=2) == 0.0
    assert recall_at_k(["x", "y", "a"], {"a"}, k=3) == 1.0


# -- MRR -----------------------------------------------------------------------


def test_mrr_first_position() -> None:
    assert mrr(["a", "b"], {"a"}) == 1.0


def test_mrr_second_position() -> None:
    assert mrr(["x", "a"], {"a"}) == 0.5


def test_mrr_absent() -> None:
    assert mrr(["x", "y"], {"a"}) == 0.0


def test_mrr_uses_best_rank_only() -> None:
    assert mrr(["x", "a", "b"], {"a", "b"}) == 0.5


# -- nDCG@K --------------------------------------------------------------------


def test_ndcg_perfect_ranking_is_one() -> None:
    assert ndcg_at_k(["a", "b"], {"a", "b"}, k=10) == 1.0


def test_ndcg_imperfect_ranking_below_one() -> None:
    score = ndcg_at_k(["x", "a"], {"a"}, k=10)
    assert 0.0 < score < 1.0


def test_ndcg_missing_document() -> None:
    assert ndcg_at_k(["x", "y"], {"a"}, k=10) == 0.0


def test_ndcg_empty_ranked_list() -> None:
    assert ndcg_at_k([], {"a"}, k=10) == 0.0


def test_ndcg_empty_relevant_is_vacuously_one() -> None:
    assert ndcg_at_k([], set(), k=10) == 1.0


def test_ndcg_cutoff_ignores_tail() -> None:
    # Релевантный документ на позиции 3 при k=2 не учитывается.
    assert ndcg_at_k(["x", "y", "a"], {"a"}, k=2) == 0.0


# -- Временна́я корректность ------------------------------------------------------


def _temporal_cases() -> list[TemporalCase]:
    return [
        TemporalCase("uk-rf:art-999-synthetic", date(2021, 6, 15), "v2020"),
        TemporalCase("uk-rf:art-999-synthetic", date(2023, 12, 31), "v2020"),
        TemporalCase("uk-rf:art-999-synthetic", date(2024, 1, 1), "v2024"),
        TemporalCase("uk-rf:art-999-synthetic", date(2026, 8, 26), "v2024"),
    ]


def test_temporal_accuracy_full(norm_store) -> None:
    accuracy, failures = temporal_accuracy(norm_store, _temporal_cases())
    assert accuracy == 1.0
    assert failures == []


def test_temporal_accuracy_detects_wrong_expectation(norm_store) -> None:
    wrong = [TemporalCase("uk-rf:art-999-synthetic", date(2022, 1, 1), "v2024")]
    accuracy, failures = temporal_accuracy(norm_store, wrong)
    assert accuracy == 0.0
    assert len(failures) == 1
    assert failures[0].expected_version_id == "v2024"


def test_temporal_accuracy_empty_case_list(norm_store) -> None:
    accuracy, failures = temporal_accuracy(norm_store, [])
    assert accuracy == 1.0
    assert failures == []

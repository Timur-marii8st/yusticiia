from __future__ import annotations

from second_opinion.api.deps import load_cases
from second_opinion.domain.facts import AggravatingFactor, CaseFacts, Qualification
from second_opinion.retrieval.case_retrieval import CaseRetriever
from tests.conftest import FIXTURES_DIR


def _criteria(article: int, part: int | None = 2, **kwargs) -> CaseFacts:
    case = CaseFacts()
    case.offense.qualifications.append(Qualification(article=article, part=part))
    for key, value in kwargs.items():
        setattr(case.offense if key == "stage" else case.procedural, key, value)
    return case


def _retriever() -> CaseRetriever:
    return CaseRetriever(load_cases(FIXTURES_DIR))


def test_case_base_expanded() -> None:
    assert _retriever().total == 22


def test_filter_by_article_only() -> None:
    matches = _retriever().search(_criteria(158), limit=50)
    assert matches
    assert all(m.case.article == 158 for m in matches)


def test_sparse_criteria_marked_as_article_only() -> None:
    matches = _retriever().search(_criteria(161, part=None), limit=50)
    assert matches
    for match in matches:
        assert any("только по статье" in reason for reason in match.reasons)


def test_each_match_has_reasons_and_no_similarity_percent() -> None:
    matches = _retriever().search(_criteria(158, part=2), limit=50)
    assert matches
    for match in matches:
        assert match.reasons, match.case.case_id
        assert all("%" not in reason for reason in match.reasons)


def test_part_match_ranks_higher() -> None:
    matches = _retriever().search(_criteria(158, part=2), limit=50)
    top = matches[0]
    assert top.case.part == 2


def test_no_qualification_returns_empty() -> None:
    assert _retriever().search(CaseFacts()) == []


def test_stage_reason_added() -> None:
    criteria = _criteria(158, part=2, stage="attempt")
    matches = _retriever().search(criteria, limit=50)
    attempt_matches = [m for m in matches if m.case.stage == "attempt"]
    assert attempt_matches
    assert any("стадия" in reason for reason in attempt_matches[0].reasons)


def test_jury_trial_matching() -> None:
    criteria = _criteria(111, part=1, jury_trial=True)
    matches = _retriever().search(criteria, limit=50)
    jury_matches = [m for m in matches if m.case.jury_trial]
    assert jury_matches
    assert any("присяжн" in reason for reason in jury_matches[0].reasons)


def test_no_substantive_similarity_returns_empty() -> None:
    # Часть не совпадает ни с одним делом, стадия — тоже: извлечённые
    # содержательные признаки не дали ни одного совпадения — честно
    # возвращаем пусто, а не дела, «близкие по статье».
    criteria = _criteria(159, part=3, stage="attempt")
    assert _retriever().search(criteria, limit=50) == []


def test_recidivism_positive_match_is_substantive() -> None:
    criteria = _criteria(158, part=2)
    criteria.aggravating.append(
        AggravatingFactor(code="63.1.а", title="рецидив", fact_ids=["f1"])
    )
    matches = _retriever().search(criteria, limit=50)
    recidivism_matches = [m for m in matches if m.case.recidivism]
    assert recidivism_matches
    assert any("рецидив" in reason for reason in recidivism_matches[0].reasons)

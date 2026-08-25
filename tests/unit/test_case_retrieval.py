from __future__ import annotations

from second_opinion.api.deps import load_cases
from second_opinion.domain.facts import CaseFacts, Qualification
from second_opinion.retrieval.case_retrieval import CaseRetriever
from tests.conftest import FIXTURES_DIR


def _criteria(article: int, part: int | None = 2, **kwargs) -> CaseFacts:
    case = CaseFacts()
    case.offense.qualifications.append(Qualification(article=article, part=part))
    for key, value in kwargs.items():
        setattr(case.offense if key == "stage" else case.procedural, key, value)
    return case


def test_filter_by_article_only() -> None:
    retriever = CaseRetriever(load_cases(FIXTURES_DIR))
    matches = retriever.search(_criteria(158), limit=50)
    assert matches
    assert all(m.case.article == 158 for m in matches)


def test_each_match_has_reasons_and_no_similarity_percent() -> None:
    retriever = CaseRetriever(load_cases(FIXTURES_DIR))
    matches = retriever.search(_criteria(158, part=2), limit=50)
    assert matches
    for match in matches:
        assert match.reasons, match.case.case_id
        assert all("%" not in reason for reason in match.reasons)


def test_part_match_ranks_higher() -> None:
    retriever = CaseRetriever(load_cases(FIXTURES_DIR))
    matches = retriever.search(_criteria(158, part=2), limit=50)
    top = matches[0]
    assert top.case.part == 2


def test_no_qualification_returns_empty() -> None:
    retriever = CaseRetriever(load_cases(FIXTURES_DIR))
    assert retriever.search(CaseFacts()) == []


def test_stage_reason_added() -> None:
    retriever = CaseRetriever(load_cases(FIXTURES_DIR))
    criteria = _criteria(158, part=2)
    criteria.offense.stage = "attempt"
    matches = retriever.search(criteria, limit=50)
    attempt_matches = [m for m in matches if m.case.stage == "attempt"]
    assert attempt_matches
    assert any("стадия" in reason for reason in attempt_matches[0].reasons)

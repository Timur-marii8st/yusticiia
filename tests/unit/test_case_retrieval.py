from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from second_opinion.api.deps import load_cases
from second_opinion.domain.cases import ComparableCase
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
    assert _retriever().total == 31


def test_real_cases_are_traceable_to_a_source() -> None:
    """Реальное дело обязано указывать, откуда оно взято.

    ``synthetic=False`` без ссылки на источник — недоказанное утверждение
    «это реальная практика», а не факт. Модель отказывается создать такую
    запись (см. ``ComparableCase._real_case_names_its_source``); здесь
    проверяется, что фикстуры на диске этому правилу подчиняются, а не
    только что модель умеет отказывать.
    """
    real_cases = [case for case in load_cases(FIXTURES_DIR) if not case.synthetic]
    assert real_cases, "в фикстурах не осталось ни одного реального дела"
    for case in real_cases:
        assert case.source_url.startswith("http"), (
            f"{case.case_id}: source_url не похож на ссылку: {case.source_url!r}"
        )


def test_real_case_without_source_is_rejected() -> None:
    with pytest.raises(ValidationError, match="source_url"):
        ComparableCase(
            case_id="no-source",
            title="дело без источника",
            court="суд",
            date=date(2026, 1, 1),
            article=158,
            synthetic=False,
        )


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


def test_group_criterion_matching() -> None:
    criteria = _criteria(159, part=2)
    criteria.offense.complicity_role = "group_with_conspiracy"
    matches = _retriever().search(criteria, limit=50)
    assert matches
    assert any("групп" in reason for match in matches for reason in match.reasons)


def test_group_criterion_counts_as_substance() -> None:
    """Только группа без иных совпадений: дела без признака группы не выдаются."""
    criteria = _criteria(158, part=None)
    criteria.offense.complicity_role = "organized_group"
    matches = _retriever().search(criteria, limit=50)
    assert all(m.case.group for m in matches)


def test_case_model_carries_no_judge_identity() -> None:
    """Сведений о судье в базе практики нет — и не должно появиться.

    Концепция ВС РФ по ИИ (приказ от 03.09.2026 № 59-П) запрещает
    персонализированный анализ судей. Запрет выполняется не оговоркой в
    документации, а устройством модели: в `ComparableCase` есть суд, но
    нет судьи, поэтому срез «как судит такой-то» построить не на чем.

    Проверка стоит здесь, чтобы поле не добавили по недосмотру: оно
    выглядело бы безобидным реквизитом дела, а открыло бы ровно то, что
    запрещено.
    """
    forbidden = {"judge", "judge_name", "judge_id", "presiding", "судья"}
    present = set(ComparableCase.model_fields) & forbidden

    assert not present, (
        f"в модели дела появились сведения о судье: {sorted(present)}. "
        "Персонализированный анализ судей запрещён — поле убрать или "
        "обосновать отдельным решением"
    )

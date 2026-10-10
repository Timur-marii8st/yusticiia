from __future__ import annotations

import pytest

from second_opinion.domain.enums import FactStatus, FactType
from second_opinion.domain.evidence import Evidence
from second_opinion.domain.facts import LegalFact
from second_opinion.fact_extraction.builder import build_case_facts


def _fact(fact_type: FactType, value: object) -> LegalFact:
    return LegalFact(
        id=f"fact-{fact_type.value}",
        type=fact_type,
        value=value,
        confidence=0.9,
        evidence=[Evidence(document_id="d", quote="q", start_offset=0, end_offset=1)],
        extraction_method="pattern",
        status=FactStatus.VERIFIED,
    )


@pytest.mark.parametrize(
    "fact_type",
    [
        FactType.MINOR_DEPENDENTS,
        FactType.SURRENDER_OR_CONFESSION,
        FactType.RESTITUTION,
        FactType.AGGRAVATING_RECIDIVISM,
    ],
)
def test_false_flag_adds_no_mitigating_or_aggravating_factor(fact_type: FactType) -> None:
    case = build_case_facts([_fact(fact_type, False)])
    assert case.mitigating == []
    assert case.aggravating == []


@pytest.mark.parametrize(
    ("fact_type", "code"),
    [
        (FactType.MINOR_DEPENDENTS, "61.2"),
        (FactType.SURRENDER_OR_CONFESSION, "61.1.и"),
        (FactType.RESTITUTION, "61.1.к"),
    ],
)
def test_true_flag_adds_mitigating_factor(fact_type: FactType, code: str) -> None:
    case = build_case_facts([_fact(fact_type, True)])
    assert [m.code for m in case.mitigating] == [code]


def test_true_recidivism_adds_aggravating_factor() -> None:
    case = build_case_facts([_fact(FactType.AGGRAVATING_RECIDIVISM, True)])
    assert [a.code for a in case.aggravating] == ["63.1.а"]


def test_minor_dependents_false_is_recorded_as_false() -> None:
    case = build_case_facts([_fact(FactType.MINOR_DEPENDENTS, False)])
    assert case.defendant.minor_dependents is False


def test_final_term_is_kept_apart_from_the_term_for_the_offense() -> None:
    case = build_case_facts(
        [_fact(FactType.PUNISHMENT_TERM, 18.0), _fact(FactType.FINAL_PUNISHMENT_TERM, 24.0)]
    )
    assert case.sentence.term_months == 18.0
    assert case.sentence.final_term_months == 24.0


def test_summary_names_both_terms() -> None:
    from second_opinion.reporting import build_summary

    facts = [
        _fact(FactType.PUNISHMENT_TYPE, "imprisonment"),
        _fact(FactType.PUNISHMENT_TERM, 18.0),
        _fact(FactType.FINAL_PUNISHMENT_TERM, 24.0),
    ]
    summary = build_summary(build_case_facts(facts), facts)
    line = next(line.text for line in summary.lines if line.text.startswith("Назначено"))
    assert "на срок 18 мес." in line
    assert "окончательно по совокупности 24 мес. (2 г.)" in line

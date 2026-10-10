"""Черновые правила ст. 68, 88, 10 УК РФ (вне реестра до сверки юристом)."""

from __future__ import annotations

from datetime import date

import pytest

from second_opinion.domain.enums import PunishmentType, VerificationStatus
from second_opinion.domain.facts import (
    AggravatingFactor,
    CaseFacts,
    MitigatingFactor,
    Qualification,
)
from second_opinion.domain.norms import LegalNorm, NormVersion, SanctionSpec
from second_opinion.legal_sources.store import NormStore
from second_opinion.rule_engine.engine import DEFAULT_RULES, RuleEngine
from second_opinion.rule_engine.rules.drafts import (
    DRAFT_RULES,
    MinorSentenceRule,
    RecidivismMinimumRule,
    RetroactivityRule,
)

WHEN = date(2025, 3, 1)


def _version(norm_id: str, vid: str, start: date, end: date | None, max_m: float, min_m: float | None = None) -> NormVersion:
    text = f"{norm_id} {vid}: лишение свободы до {max_m:g} мес."
    return NormVersion(
        norm_id=norm_id, version_id=vid, text=text, effective_from=start, effective_to=end,
        sanctions=[SanctionSpec(punishment_type=PunishmentType.IMPRISONMENT, min_months=min_m, max_months=max_m)],
        source_document="тест", retrieved_at=date(2026, 10, 10), sha256=NormVersion.compute_sha256(text),
        verification_status=VerificationStatus.VERIFIED,
    )


def _store(max_m: float = 84, min_m: float | None = None, checked: bool = True) -> NormStore:
    store = NormStore()
    norm = LegalNorm(
        norm_id="uk-rf:art-161-part-2", code="УК РФ", article=161, part=2, title="Грабёж",
        amendments_checked_through=date(2026, 10, 1) if checked else None,
    )
    store.add_norm(norm, [_version(norm.norm_id, "v1", date(1997, 1, 1), None, max_m, min_m)])
    return store


def _case(*, term: float | None = 14, kind: str = "imprisonment", recidivism: bool = True, mitigating: bool = False,
          age: int | None = 30, prior: bool | None = True, stage: str = "completed", special: bool | None = None,
          hours: float | None = None) -> CaseFacts:
    case = CaseFacts()
    case.offense.qualifications.append(Qualification(article=161, part=2))
    case.offense.stage = stage
    case.procedural.special_procedure = special
    case.sentence.punishment_type = kind
    case.sentence.term_months = term
    case.sentence.hours = hours
    case.defendant.age = age
    case.defendant.prior_convictions = prior
    if recidivism:
        case.aggravating.append(AggravatingFactor(code="63.1.а", title="рецидив"))
    if mitigating:
        case.mitigating.append(MitigatingFactor(code="61.1.и", title="явка с повинной"))
    return case


def _run(rule, case: CaseFacts, store: NormStore, when: date = WHEN):
    return RuleEngine(rules=[rule]).evaluate(case, store, when)[0]


def test_drafts_are_not_registered() -> None:
    assert not set(DRAFT_RULES) & set(DEFAULT_RULES)


@pytest.mark.parametrize(
    ("term", "mitigating", "status", "phrase"),
    [(14, False, "WARNING", "возможно нарушение ч. 2 ст. 68"), (14, True, "WARNING", "ч. 3 ст. 68"), (28, False, "PASS", "")],
)
def test_recidivism_third_of_maximum(term, mitigating, status, phrase) -> None:
    result = _run(RecidivismMinimumRule, _case(term=term, mitigating=mitigating), _store())
    assert result.status.value == status
    assert phrase in result.explanation
    if status != "PASS":
        assert result.numbers["limit_months"] == 28


def test_no_recidivism_is_not_applicable() -> None:
    assert _run(RecidivismMinimumRule, _case(recidivism=False), _store()).status.value == "NOT_APPLICABLE"


def test_milder_kind_under_recidivism_is_flagged() -> None:
    result = _run(RecidivismMinimumRule, _case(kind="correctional_labor"), _store())
    assert result.status.value == "WARNING"
    assert "наиболее строгий вид" in result.explanation


def test_attempt_needs_special_procedure_for_the_formula() -> None:
    assert _run(RecidivismMinimumRule, _case(stage="attempt"), _store()).status.value == "UNKNOWN"
    result = _run(RecidivismMinimumRule, _case(stage="attempt", special=True, term=20), _store())
    assert result.numbers["limit_months"] == 21
    assert result.status.value == "WARNING"


def test_minor_first_time_small_gravity_cannot_get_imprisonment() -> None:
    result = _run(MinorSentenceRule, _case(age=17, prior=False, term=6), _store(max_m=24))
    assert result.status.value == "WARNING"
    assert "небольшой тяжести" in result.explanation


def test_minor_sixteen_medium_gravity_depends_on_age_at_the_deed() -> None:
    result = _run(MinorSentenceRule, _case(age=16, prior=False, term=10), _store(max_m=60))
    assert result.status.value == "UNKNOWN"
    young = _run(MinorSentenceRule, _case(age=15, prior=False, term=10), _store(max_m=60))
    assert young.status.value == "WARNING"


def test_minor_hours_and_adult_age() -> None:
    hours = _run(MinorSentenceRule, _case(age=16, kind="compulsory_labor", term=None, hours=200), _store())
    assert hours.status.value == "WARNING" and "40–160" in hours.explanation
    assert _run(MinorSentenceRule, _case(age=30), _store()).status.value == "NOT_APPLICABLE"
    assert _run(MinorSentenceRule, _case(age=18), _store()).status.value == "UNKNOWN"


def test_later_milder_edition_raises_article_10() -> None:
    store = NormStore()
    norm = LegalNorm(norm_id="uk-rf:art-158-part-2", code="УК РФ", article=158, part=2, title="Кража")
    store.add_norm(norm, [
        _version(norm.norm_id, "v-1996", date(1997, 1, 1), date(2003, 12, 15), 72, 24),
        _version(norm.norm_id, "v-2003", date(2003, 12, 16), None, 60),
    ])
    case = _case()
    case.offense.qualifications[0] = Qualification(article=158, part=2)
    early = _run(RetroactivityRule, case, store, date(2003, 6, 10))
    assert early.status.value == "WARNING" and "ст. 10" in early.explanation
    late = _run(RetroactivityRule, case, store, date(2025, 3, 15))
    assert late.status.value == "UNKNOWN"


def test_no_later_edition_with_checked_history_passes() -> None:
    assert _run(RetroactivityRule, _case(), _store(checked=True)).status.value == "PASS"

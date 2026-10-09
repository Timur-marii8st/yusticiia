"""Пределы санкции для штрафа (рубли) и обязательных работ (часы) в R-001."""

from __future__ import annotations

from datetime import date

import pytest

from second_opinion.domain.enums import PunishmentType, VerificationStatus
from second_opinion.domain.facts import CaseFacts, Qualification
from second_opinion.domain.norms import LegalNorm, NormVersion, SanctionSpec, sanction_for
from second_opinion.legal_sources.store import NormStore
from second_opinion.rule_engine.engine import RuleEngine

TEXT = "Синтетическая норма для проверки пределов штрафа и обязательных работ."


def _store() -> NormStore:
    store = NormStore()
    norm = LegalNorm(norm_id="uk-rf:art-158-part-1", code="УК РФ", article=158, part=1, title="Кража")
    version = NormVersion(
        norm_id=norm.norm_id,
        version_id="v-test",
        text=TEXT,
        sanctions=[
            SanctionSpec(punishment_type=PunishmentType.IMPRISONMENT, max_months=24),
            SanctionSpec(
                punishment_type=PunishmentType.FINE,
                max_rubles=80_000,
                effective_from=date(2011, 12, 8),
                pending_review=True,
            ),
            SanctionSpec(
                punishment_type=PunishmentType.FINE,
                max_rubles=60_000,
                effective_to=date(2011, 12, 7),
                pending_review=True,
            ),
            SanctionSpec(
                punishment_type=PunishmentType.COMPULSORY_LABOR,
                min_hours=60,
                max_hours=360,
                pending_review=True,
            ),
        ],
        effective_from=date(2003, 12, 16),
        source_document="тест",
        retrieved_at=date(2026, 10, 9),
        sha256=NormVersion.compute_sha256(TEXT),
        verification_status=VerificationStatus.VERIFIED,
    )
    store.add_norm(norm, [version])
    return store


def _case(kind: str, *, hours: float | None = None, rubles: float | None = None) -> CaseFacts:
    case = CaseFacts()
    case.offense.qualifications.append(Qualification(article=158, part=1))
    case.sentence.punishment_type = kind
    case.sentence.hours = hours
    case.sentence.fine_rubles = rubles
    return case


def _r001(case: CaseFacts, when: date = date(2025, 3, 1)):
    evaluations = RuleEngine().evaluate(case, _store(), when)
    return next(e for e in evaluations if e.rule_id == "R-001")


def test_sanction_entry_applies_only_inside_its_interval() -> None:
    sanctions = _store().get_norm("uk-rf:art-158-part-1", date(2025, 1, 1)).sanctions
    assert sanction_for(sanctions, "fine", date(2025, 1, 1)).max_rubles == 80_000
    assert sanction_for(sanctions, "fine", date(2010, 1, 1)).max_rubles == 60_000
    assert sanction_for(sanctions, "imprisonment", date(2010, 1, 1)).max_months == 24


def test_fine_within_the_limit_passes_and_says_the_limit_is_not_reviewed() -> None:
    evaluation = _r001(_case("fine", rubles=50_000))
    assert evaluation.status.value == "PASS"
    assert "не сверен юристом" in evaluation.explanation
    assert evaluation.numbers == {"amount": 50_000.0, "max": 80_000.0}


def test_fine_above_the_limit_of_its_date_fails() -> None:
    assert _r001(_case("fine", rubles=70_000), date(2011, 1, 1)).status.value == "FAIL"
    assert _r001(_case("fine", rubles=70_000), date(2012, 1, 1)).status.value == "PASS"


@pytest.mark.parametrize(("hours", "status"), [(280, "PASS"), (480, "FAIL"), (40, "WARNING")])
def test_compulsory_labor_hours_are_checked_against_the_sanction(hours: float, status: str) -> None:
    assert _r001(_case("compulsory_labor", hours=hours)).status.value == status


def test_missing_hours_are_named_not_guessed() -> None:
    evaluation = _r001(_case("compulsory_labor"))
    assert evaluation.status.value == "UNKNOWN"
    assert evaluation.missing == ["срок обязательных работ в часах"]


def test_norm_without_limits_for_the_kind_is_unknown() -> None:
    store = NormStore()
    norm = LegalNorm(norm_id="uk-rf:art-159-part-1", code="УК РФ", article=159, part=1, title="Мошенничество")
    version = NormVersion(
        norm_id=norm.norm_id,
        version_id="v-test",
        text=TEXT,
        sanctions=[SanctionSpec(punishment_type=PunishmentType.IMPRISONMENT, max_months=24)],
        effective_from=date(2003, 12, 16),
        source_document="тест",
        retrieved_at=date(2026, 10, 9),
        sha256=NormVersion.compute_sha256(TEXT),
    )
    store.add_norm(norm, [version])
    case = _case("fine", rubles=10_000)
    case.offense.qualifications[0] = Qualification(article=159, part=1)
    evaluation = next(
        e for e in RuleEngine().evaluate(case, store, date(2025, 1, 1)) if e.rule_id == "R-001"
    )
    assert evaluation.status.value == "UNKNOWN"
    assert "предел данного вида наказания не внесён" in evaluation.explanation


def test_sanction_interval_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="effective_to раньше"):
        SanctionSpec(
            punishment_type=PunishmentType.FINE,
            effective_from=date(2020, 1, 1),
            effective_to=date(2019, 1, 1),
        )

"""Правки даты деяния меняют редакцию нормы, но не явный выбор пользователя."""

from __future__ import annotations

import json
from datetime import date

import pytest

from second_opinion.domain.analysis import AnalysisReport
from second_opinion.domain.enums import FactStatus, FactType
from second_opinion.fact_values import FactValidationError

TODAY = "2026-10-10"
OFFENSE_DATE = "2022-05-10"
LATER_DATE = "2025-05-10"


class _Today(date):
    @classmethod
    def today(cls):
        return cls.fromisoformat(TODAY)


@pytest.fixture(autouse=True)
def fixed_analysis_date(monkeypatch):
    monkeypatch.setattr("second_opinion.pipeline.derivation.date", _Today)


def _analyze(pipeline, *, pattern_date=False, model_date=False, explicit=None):
    when = "10 мая 2022 года" if pattern_date else "10.05.2022"
    text = (
        f"УСТАНОВИЛ: Подсудимый совершил преступление {when}. "
        "В документе также указана дата 10.05.2025. "
        "ПРИГОВОРИЛ: Признать виновным в совершении преступления, "
        "предусмотренного ч. 1 ст. 999 УК РФ, и назначить наказание "
        "в виде лишения свободы на срок 48 месяцев."
    )
    if model_date:
        pipeline.llm_provider.set_canned(
            "fact_extraction",
            json.dumps({"facts": [{
                "type": "date_of_offense", "value": OFFENSE_DATE,
                "confidence": 0.9, "quote": "10.05.2022",
            }]}),
        )
    document = pipeline.ingest("temporal.txt", text.encode())
    return pipeline.analyze(document.document_id, applicable_at=explicit)


def _date_fact(report):
    return next(f for f in report.facts if f.type is FactType.DATE_OF_OFFENSE)


def _assert_applied(report, when, source, version):
    assert report.applicable_at == when
    assert report.applicable_at_source == source
    assert report.applicable_at_assumed is (source == "assumed")
    evaluation = next(e for e in report.evaluations if e.rule_id == "R-001")
    assert evaluation.norms_used[0].version_id == version
    assert evaluation.status.value == ("FAIL" if version == "v2020" else "PASS")


def _store_legacy(pipeline, report):
    payload = report.model_dump()
    payload.pop("applicable_at_source")
    pipeline._analyses.save(AnalysisReport.model_validate(payload))
    return pipeline.get_analysis(report.analysis_id)


def test_confirmed_llm_date_changes_the_applied_version(pipeline):
    report = _analyze(pipeline, model_date=True)
    _assert_applied(report, TODAY, "assumed", "v2024")
    fact = _date_fact(report)
    assert fact.status is FactStatus.LIKELY
    assert report.case_facts.applicable_at is None

    updated = pipeline.update_fact(report.analysis_id, fact.id, status="VERIFIED")

    _assert_applied(updated, OFFENSE_DATE, "fact", "v2020")
    assert updated.case_facts.applicable_at == updated.applicable_at
    assert not any("Дата события" in c.title for c in updated.concerns)
    stored = pipeline.get_analysis(report.analysis_id)
    _assert_applied(stored, OFFENSE_DATE, "fact", "v2020")


def test_correcting_an_extracted_date_recomputes_the_version(pipeline):
    report = _analyze(pipeline, pattern_date=True)
    _assert_applied(report, OFFENSE_DATE, "fact", "v2020")

    updated = pipeline.update_fact(report.analysis_id, _date_fact(report).id, value=LATER_DATE)

    _assert_applied(updated, LATER_DATE, "fact", "v2024")
    assert updated.case_facts.applicable_at == updated.applicable_at


def test_adding_a_date_replaces_the_assumed_date(pipeline):
    report = _analyze(pipeline)
    _assert_applied(report, TODAY, "assumed", "v2024")

    updated = pipeline.add_fact(
        report.analysis_id, fact_type="date_of_offense",
        value=OFFENSE_DATE, quote="10.05.2022",
    )

    _assert_applied(updated, OFFENSE_DATE, "fact", "v2020")
    assert updated.case_facts.applicable_at == updated.applicable_at


def test_excluding_and_restoring_the_only_date_updates_the_assumption(pipeline):
    report = _analyze(pipeline, pattern_date=True)
    fact_id = _date_fact(report).id

    excluded = pipeline.update_fact(
        report.analysis_id, fact_id, status="NOT_FOUND", reason="Дата извлечена неверно"
    )

    _assert_applied(excluded, TODAY, "assumed", "v2024")
    assert excluded.case_facts.applicable_at is None
    assert any("Дата события" in c.title for c in excluded.concerns)

    restored = pipeline.update_fact(report.analysis_id, fact_id, status="VERIFIED")
    _assert_applied(restored, OFFENSE_DATE, "fact", "v2020")


def test_excluding_the_selected_date_reverts_to_another_active_date(pipeline):
    report = _analyze(pipeline, pattern_date=True)
    original_id = _date_fact(report).id
    updated = pipeline.add_fact(
        report.analysis_id, fact_type="date_of_offense", value=LATER_DATE, quote="10.05.2025"
    )
    _assert_applied(updated, LATER_DATE, "fact", "v2024")
    added = next(
        f for f in updated.facts if f.type is FactType.DATE_OF_OFFENSE and f.id != original_id
    )

    excluded = pipeline.update_fact(
        report.analysis_id, added.id, status="NOT_FOUND", reason="Дата относится к другому событию"
    )

    _assert_applied(excluded, OFFENSE_DATE, "fact", "v2020")
    assert excluded.case_facts.applicable_at == OFFENSE_DATE


def test_excluding_a_manual_date_does_not_activate_a_pending_llm_date(pipeline):
    report = _analyze(pipeline, model_date=True)
    model_id = _date_fact(report).id
    updated = pipeline.add_fact(
        report.analysis_id, fact_type="date_of_offense", value=LATER_DATE, quote="10.05.2025"
    )
    manual = next(
        f for f in updated.facts if f.type is FactType.DATE_OF_OFFENSE and f.id != model_id
    )

    excluded = pipeline.update_fact(
        report.analysis_id, manual.id, status="NOT_FOUND", reason="Дата не подтверждена"
    )

    _assert_applied(excluded, TODAY, "assumed", "v2024")
    assert excluded.case_facts.applicable_at is None
    assert next(f for f in excluded.facts if f.id == model_id).status is FactStatus.LIKELY


@pytest.mark.parametrize("operation", ["confirm", "correct", "exclude", "add"])
def test_explicit_override_survives_date_edits_even_when_it_matches_the_fact(pipeline, operation):
    report = _analyze(pipeline, model_date=True, explicit=OFFENSE_DATE)
    fact_id = _date_fact(report).id
    if operation == "confirm":
        updated = pipeline.update_fact(report.analysis_id, fact_id, status="VERIFIED")
    elif operation == "correct":
        updated = pipeline.update_fact(report.analysis_id, fact_id, value=LATER_DATE)
    elif operation == "exclude":
        updated = pipeline.update_fact(
            report.analysis_id, fact_id, status="NOT_FOUND", reason="Не относится к делу"
        )
    else:
        updated = pipeline.add_fact(
            report.analysis_id, fact_type="date_of_offense", value=LATER_DATE, quote="10.05.2025"
        )

    _assert_applied(updated, OFFENSE_DATE, "explicit", "v2020")
    _assert_applied(pipeline.get_analysis(report.analysis_id), OFFENSE_DATE, "explicit", "v2020")


def test_legacy_assumed_date_can_be_replaced_by_a_confirmed_fact(pipeline):
    report = _store_legacy(pipeline, _analyze(pipeline, model_date=True))
    assert report.applicable_at_source == "legacy"
    assert report.applicable_at_assumed

    updated = pipeline.update_fact(report.analysis_id, _date_fact(report).id, status="VERIFIED")

    _assert_applied(updated, OFFENSE_DATE, "fact", "v2020")


@pytest.mark.parametrize("explicit", [None, OFFENSE_DATE])
def test_legacy_date_of_unknown_origin_is_preserved(pipeline, explicit):
    """Совпадение с датой факта не доказывает отсутствие старого override."""
    report = _store_legacy(pipeline, _analyze(pipeline, pattern_date=True, explicit=explicit))
    assert report.applicable_at_source == "legacy"
    assert not report.applicable_at_assumed

    updated = pipeline.update_fact(report.analysis_id, _date_fact(report).id, value=LATER_DATE)

    _assert_applied(updated, OFFENSE_DATE, "legacy", "v2020")
    assert updated.case_facts.applicable_at == LATER_DATE


def test_unrelated_fact_edit_does_not_shift_an_assumed_date(pipeline, monkeypatch):
    report = _analyze(pipeline, model_date=True)
    term = next(f for f in report.facts if f.type is FactType.PUNISHMENT_TERM)

    class Later(date):
        @classmethod
        def today(cls):
            return cls(2027, 1, 1)

    monkeypatch.setattr("second_opinion.pipeline.derivation.date", Later)
    updated = pipeline.update_fact(report.analysis_id, term.id, value=47)

    _assert_applied(updated, TODAY, "assumed", "v2024")
    assert updated.case_facts.applicable_at is None


def test_invalid_date_edit_is_rejected_instead_of_selecting_today(pipeline):
    report = _analyze(pipeline, pattern_date=True)

    with pytest.raises(FactValidationError, match="date_of_offense"):
        pipeline.update_fact(report.analysis_id, _date_fact(report).id, value="не дата")

    stored = pipeline.get_analysis(report.analysis_id)
    _assert_applied(stored, OFFENSE_DATE, "fact", "v2020")
    assert _date_fact(stored).value == OFFENSE_DATE

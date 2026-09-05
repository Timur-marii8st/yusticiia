from __future__ import annotations

import json

import pytest

from second_opinion.domain.enums import ExtractionMethod, FactStatus, FactType
from second_opinion.pipeline import AnalysisNotFound, FactNotFound
from tests.conftest import SAMPLE_CLEAN


def _report(pipeline):
    document = pipeline.ingest(SAMPLE_CLEAN.name, SAMPLE_CLEAN.read_bytes())
    return document, pipeline.analyze(document.document_id)


def _fact(report, fact_type: FactType):
    return next(f for f in report.facts if f.type is fact_type)


def test_excluding_fact_changes_evaluations(pipeline) -> None:
    _, report = _report(pipeline)
    assert report.case_facts.mitigating  # возмещение ущерба извлечено

    restitution = next(
        f for f in report.facts if f.type is FactType.RESTITUTION
    )
    updated = pipeline.update_fact(
        report.analysis_id, restitution.id, status="NOT_FOUND"
    )

    updated_fact = next(f for f in updated.facts if f.id == restitution.id)
    assert updated_fact.status is FactStatus.NOT_FOUND
    # без пп. «и»/«к» предел ч. 1 ст. 62 УК РФ не применяется
    r004 = next(e for e in updated.evaluations if e.rule_id == "R-004")
    assert "не применимо" in r004.headline
    remaining_codes = {m.code for m in updated.case_facts.mitigating}
    assert not (remaining_codes & {"61.1.и", "61.1.к"})


def test_excluded_fact_remains_visible_in_report(pipeline) -> None:
    _, report = _report(pipeline)
    restitution = next(f for f in report.facts if f.type is FactType.RESTITUTION)
    updated = pipeline.update_fact(
        report.analysis_id, restitution.id, status="NOT_FOUND"
    )
    assert any(f.id == restitution.id for f in updated.facts)


def test_confirming_likely_llm_fact_promotes_it(pipeline) -> None:
    pipeline.llm_provider.set_canned(
        "fact_extraction",
        json.dumps(
            {
                "facts": [
                    {
                        "type": "health_factor",
                        "value": "семейное положение",
                        "confidence": 0.6,
                        "quote": "семейное положение",
                    }
                ]
            }
        ),
    )
    _, report = _report(pipeline)
    likely = [f for f in report.facts if f.status is FactStatus.LIKELY]
    assert likely
    fact = likely[0]

    updated = pipeline.update_fact(report.analysis_id, fact.id, status="VERIFIED")
    confirmed = next(f for f in updated.facts if f.id == fact.id)
    assert confirmed.status is FactStatus.VERIFIED
    assert confirmed.extraction_method is ExtractionMethod.USER


def test_value_correction_recomputes_rules(pipeline) -> None:
    _, report = _report(pipeline)
    term = _fact(report, FactType.PUNISHMENT_TERM)
    assert term.value == 18.0

    # Судья исправил срок на 45 мес.: особый порядок → предел 2/3 (40 мес.)
    updated = pipeline.update_fact(report.analysis_id, term.id, value=45.0)
    corrected = next(f for f in updated.facts if f.id == term.id)
    assert corrected.status is FactStatus.VERIFIED
    assert corrected.extraction_method is ExtractionMethod.USER

    r003 = next(e for e in updated.evaluations if e.rule_id == "R-003")
    assert r003.status.value == "FAIL"
    assert r003.numbers["term_months"] == 45.0
    assert updated.case_facts.sentence.term_months == 45.0


def test_unknown_statuses_and_ids_rejected(pipeline) -> None:
    _, report = _report(pipeline)
    term = _fact(report, FactType.PUNISHMENT_TERM)
    with pytest.raises(ValueError):
        pipeline.update_fact(report.analysis_id, term.id, status="CONFLICT")
    with pytest.raises(FactNotFound):
        pipeline.update_fact(report.analysis_id, "no-such-fact", status="VERIFIED")
    with pytest.raises(AnalysisNotFound):
        pipeline.update_fact("no-such-analysis", term.id, status="VERIFIED")


def test_update_is_persisted(pipeline) -> None:
    _, report = _report(pipeline)
    term = _fact(report, FactType.PUNISHMENT_TERM)
    pipeline.update_fact(report.analysis_id, term.id, value=25.0)
    loaded = pipeline.get_analysis(report.analysis_id)
    assert loaded is not None
    assert loaded.case_facts.sentence.term_months == 25.0

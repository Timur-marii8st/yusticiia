"""Факты модели не влияют на проверки, пока судья их не подтвердил."""

from __future__ import annotations

import json

from second_opinion.domain.enums import FactStatus, FactType
from second_opinion.pipeline.derivation import active_facts

QUOTE = "сам пришёл в отдел полиции и рассказал о краже"
PAYLOAD = {
    "facts": [
        {"type": "surrender_or_confession", "value": True, "confidence": 0.8, "quote": QUOTE}
    ]
}


def _mitigating_codes(report) -> set[str]:
    return {m.code for m in report.case_facts.mitigating}


def test_active_facts_exclude_excluded_and_pending_ones() -> None:
    from second_opinion.domain.evidence import Evidence
    from second_opinion.domain.facts import LegalFact

    def fact(status: FactStatus) -> LegalFact:
        return LegalFact(
            id=f"f-{status.value}",
            type=FactType.RESTITUTION,
            value=True,
            confidence=0.9,
            evidence=[Evidence(document_id="d", quote="q", start_offset=0, end_offset=1)],
            extraction_method="pattern",
            status=status,
        )

    kept = active_facts([fact(s) for s in FactStatus])
    assert {f.status for f in kept} == {
        FactStatus.VERIFIED,
        FactStatus.UNCERTAIN,
        FactStatus.CONFLICT,
    }


def test_model_fact_waits_for_confirmation_and_then_enters_the_checks(
    pipeline, sample_clean_text
) -> None:
    pipeline.llm_provider.set_canned("fact_extraction", json.dumps(PAYLOAD))
    text = sample_clean_text + f"\nПодсудимый {QUOTE}.\n"
    document = pipeline.ingest("case.txt", text.encode("utf-8"))

    report = pipeline.analyze(document.document_id)
    model_fact = next(f for f in report.facts if f.extraction_method.value == "llm")
    assert model_fact.status is FactStatus.LIKELY
    assert "61.1.и" not in _mitigating_codes(report)
    concern = next(c for c in report.concerns if c.title.startswith("Факты модели ждут подтверждения"))
    assert model_fact.id in concern.fact_ids

    confirmed = pipeline.update_fact(
        report.analysis_id, model_fact.id, status="VERIFIED", reason=None
    )
    assert "61.1.и" in _mitigating_codes(confirmed)
    assert not any(c.title.startswith("Факты модели ждут подтверждения") for c in confirmed.concerns)


def test_no_concern_when_the_model_found_nothing(pipeline, sample_clean_text) -> None:
    document = pipeline.ingest("case.txt", sample_clean_text.encode("utf-8"))
    report = pipeline.analyze(document.document_id)
    assert not any(c.title.startswith("Факты модели") for c in report.concerns)

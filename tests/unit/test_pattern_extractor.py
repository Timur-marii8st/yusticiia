from __future__ import annotations

from second_opinion.domain.documents import Document
from second_opinion.domain.enums import FactStatus, FactType
from second_opinion.fact_extraction.pattern_extractor import PatternFactExtractor


def _document(text: str) -> Document:
    return Document(
        document_id="doc-test",
        filename="t.txt",
        content_type="text/plain",
        text=text,
        sha256="0" * 64,
    )


def _facts(text: str) -> list:
    return PatternFactExtractor().extract(_document(text))


def _by_type(facts: list, fact_type: FactType) -> list:
    return [f for f in facts if f.type is fact_type]


def test_all_evidence_quotes_match_text_by_offsets(sample_clean_text: str) -> None:
    document = _document(sample_clean_text)
    facts = PatternFactExtractor().extract(document)
    assert facts
    for fact in facts:
        for evidence in fact.evidence:
            fragment = document.text[evidence.start_offset:evidence.end_offset]
            assert fragment == evidence.quote, (fact.type, fact.id)


def test_clean_sample_extracts_expected_facts(sample_clean_text: str) -> None:
    facts = _facts(sample_clean_text)
    types = {f.type for f in facts}
    assert FactType.QUALIFICATION in types
    assert FactType.DEFENDANT_AGE in types
    assert FactType.MINOR_DEPENDENTS in types
    assert FactType.GUILTY_PLEA in types
    assert FactType.RESTITUTION in types
    assert FactType.SPECIAL_PROCEDURE in types
    assert FactType.PUNISHMENT_TYPE in types
    assert FactType.PUNISHMENT_TERM in types
    assert FactType.SUSPENDED_SENTENCE in types
    assert FactType.DATE_OF_OFFENSE in types


def test_qualification_value(sample_clean_text: str) -> None:
    qualification = _by_type(_facts(sample_clean_text), FactType.QUALIFICATION)[0]
    assert qualification.value == {"code": "УК РФ", "article": 158, "part": 2}


def test_sentence_term_is_18_months(sample_clean_text: str) -> None:
    term = _by_type(_facts(sample_clean_text), FactType.PUNISHMENT_TERM)[0]
    assert term.value == 18.0


def test_date_of_offense(sample_clean_text: str) -> None:
    date_fact = _by_type(_facts(sample_clean_text), FactType.DATE_OF_OFFENSE)[0]
    assert date_fact.value == "2025-03-15"


def test_attempt_stage_detected(sample_violation_text: str) -> None:
    stage = _by_type(_facts(sample_violation_text), FactType.OFFENSE_STAGE)[0]
    assert stage.value == "attempt"


def test_violation_sample_term_30_months(sample_violation_text: str) -> None:
    term = _by_type(_facts(sample_violation_text), FactType.PUNISHMENT_TERM)[0]
    assert term.value == 30.0


def test_negative_prior_conviction_not_extracted(sample_clean_text: str) -> None:
    # «ранее не судимого» не должно извлекаться как наличие судимости
    assert not _by_type(_facts(sample_clean_text), FactType.PRIOR_CONVICTIONS)


def test_general_part_article_is_not_qualification() -> None:
    text = "На основании ст. 73 УК РФ наказание считать условным."
    assert not _by_type(_facts(text), FactType.QUALIFICATION)


def test_pattern_facts_are_verified(sample_clean_text: str) -> None:
    facts = _facts(sample_clean_text)
    assert all(f.status is FactStatus.VERIFIED for f in facts)
    assert all(f.evidence for f in facts)


def test_sentence_taken_from_resolutive_part() -> None:
    text = (
        "Санкция предусматривает лишение свободы на срок до пяти лет. "
        "Суд решил назначить наказание в виде лишения свободы на срок 2 года."
    )
    term = _by_type(_facts(text), FactType.PUNISHMENT_TERM)[0]
    assert term.value == 24.0

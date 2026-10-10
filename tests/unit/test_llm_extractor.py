from __future__ import annotations

import json

from second_opinion.domain.documents import Document
from second_opinion.domain.enums import FactStatus, FactType
from second_opinion.fact_extraction.llm_extractor import PROMPT_VERSION, LLMFactExtractor
from second_opinion.llm.mock_provider import MockLLMProvider

PROMPT_KEY = "fact_extraction"


def _document(text: str) -> Document:
    return Document(
        document_id="doc-llm",
        filename="t.txt",
        content_type="text/plain",
        text=text,
        sha256="0" * 64,
    )


def _provider(payload: dict | str | None) -> MockLLMProvider:
    canned = {}
    if payload is not None:
        canned[PROMPT_KEY] = payload if isinstance(payload, str) else json.dumps(payload)
    return MockLLMProvider(canned)


TEXT = "Подсудимый имеет несовершеннолетнего ребенка и страдает хроническим заболеванием."


def test_llm_fact_gets_likely_status_and_offsets() -> None:
    payload = {
        "facts": [
            {
                "type": "health_factor",
                "value": "хроническим заболеванием",
                "confidence": 0.7,
                "quote": "хроническим заболеванием",
            }
        ]
    }
    extractor = LLMFactExtractor(_provider(payload))
    facts = extractor.extract(_document(TEXT), known_keys=set())
    assert len(facts) == 1
    fact = facts[0]
    assert fact.status is FactStatus.LIKELY
    assert fact.type is FactType.HEALTH_FACTOR
    evidence = fact.evidence[0]
    assert TEXT[evidence.start_offset:evidence.end_offset] == evidence.quote
    assert fact.prompt_version == "v3"


def test_llm_fact_with_absent_quote_is_dropped() -> None:
    payload = {
        "facts": [
            {
                "type": "health_factor",
                "value": "x",
                "confidence": 0.9,
                "quote": "такого текста в документе нет",
            }
        ]
    }
    extractor = LLMFactExtractor(_provider(payload))
    assert extractor.extract(_document(TEXT), known_keys=set()) == []


def test_llm_fact_duplicated_with_pattern_is_skipped() -> None:
    payload = {
        "facts": [
            {
                "type": "minor_dependents",
                "value": True,
                "confidence": 0.9,
                "quote": "несовершеннолетнего ребенка",
            }
        ]
    }
    extractor = LLMFactExtractor(_provider(payload))
    known = {(FactType.MINOR_DEPENDENTS.value, "True")}
    assert extractor.extract(_document(TEXT), known_keys=known) == []


def test_invalid_llm_json_yields_no_facts() -> None:
    extractor = LLMFactExtractor(_provider("{не валидный json"))
    assert extractor.extract(_document(TEXT), known_keys=set()) == []


def test_empty_llm_response_yields_no_facts() -> None:
    extractor = LLMFactExtractor(MockLLMProvider())
    assert extractor.extract(_document(TEXT), known_keys=set()) == []


def test_entries_with_empty_quote_do_not_discard_valid_facts() -> None:
    payload = {
        "facts": [
            {
                "type": "health_factor",
                "value": "хроническим заболеванием",
                "confidence": 0.7,
                "quote": "хроническим заболеванием",
            },
            {"type": "offense_stage", "value": None, "confidence": 0.0, "quote": ""},
            {"type": "jury_trial", "value": None, "confidence": 0.0, "quote": "   "},
        ]
    }
    provider = _provider(payload)
    facts = LLMFactExtractor(provider).extract(_document(TEXT), known_keys=set())
    assert [f.type for f in facts] == [FactType.HEALTH_FACTOR]


DATED_TEXT = "Смирнов А.В., возраст 34 года, совершил преступление 15 марта 2025 года."


def _fact(fact_type: str, value: object, quote: str) -> dict:
    return {"type": fact_type, "value": value, "confidence": 0.9, "quote": quote}


def test_llm_date_in_free_text_is_dropped() -> None:
    payload = {"facts": [_fact("date_of_offense", "15 марта 2025 года", "15 марта 2025 года")]}
    facts = LLMFactExtractor(_provider(payload)).extract(_document(DATED_TEXT), known_keys=set())
    assert facts == []


def test_llm_does_not_override_scalar_fact_found_by_patterns() -> None:
    payload = {"facts": [_fact("date_of_offense", "2024-01-01", "15 марта 2025 года")]}
    known = {(FactType.DATE_OF_OFFENSE.value, "2025-03-15")}
    facts = LLMFactExtractor(_provider(payload)).extract(_document(DATED_TEXT), known_keys=known)
    assert facts == []


def test_llm_fills_scalar_fact_missing_from_patterns() -> None:
    payload = {"facts": [_fact("defendant_age", 34, "возраст 34 года")]}
    facts = LLMFactExtractor(_provider(payload)).extract(_document(DATED_TEXT), known_keys=set())
    assert [(f.type, f.value) for f in facts] == [(FactType.DEFENDANT_AGE, 34)]


def test_llm_value_of_wrong_shape_is_dropped() -> None:
    payload = {"facts": [_fact("prior_convictions", [], "Смирнов А.В.")]}
    facts = LLMFactExtractor(_provider(payload)).extract(_document(DATED_TEXT), known_keys=set())
    assert facts == []


def test_llm_scalar_fact_is_accepted_once() -> None:
    payload = {
        "facts": [
            _fact("defendant_age", 34, "возраст 34 года"),
            _fact("defendant_age", 35, "Смирнов А.В."),
        ]
    }
    facts = LLMFactExtractor(_provider(payload)).extract(_document(DATED_TEXT), known_keys=set())
    assert [f.value for f in facts] == [34]


def test_prompt_lists_every_fact_type_and_the_accepted_values() -> None:
    import re

    from second_opinion.domain.enums import FactType, OffenseStage, PunishmentType
    from second_opinion.fact_values import GROUP_ROLES
    from second_opinion.prompts import load_prompt

    text = load_prompt("fact_extraction", PROMPT_VERSION).text
    listed = re.search(r'"type": "<один из: ([^>]+)>"', text)
    assert listed is not None
    assert set(listed.group(1).split("|")) == {t.value for t in FactType}
    for value in (
        *(s.value for s in OffenseStage),
        *(p.value for p in PunishmentType),
        *GROUP_ROLES,
    ):
        assert f"`{value}`" in text, value


def test_llm_false_flags_are_dropped() -> None:
    payload = {
        "facts": [
            _fact("restitution", False, "Подсудимый ранее не судим."),
            _fact("prior_convictions", False, "Подсудимый ранее не судим."),
            _fact("aggravating_recidivism", False, "Подсудимый ранее не судим."),
        ]
    }
    facts = LLMFactExtractor(_provider(payload)).extract(
        _document("Подсудимый ранее не судим."), known_keys=set()
    )
    assert facts == []


def test_llm_true_flag_is_kept() -> None:
    payload = {"facts": [_fact("restitution", True, "полностью возместил ущерб")]}
    facts = LLMFactExtractor(_provider(payload)).extract(
        _document("Подсудимый полностью возместил ущерб."), known_keys=set()
    )
    assert [(f.type, f.value) for f in facts] == [(FactType.RESTITUTION, True)]


def test_short_document_is_sent_whole() -> None:
    from second_opinion.fact_extraction.llm_extractor import fit_document

    assert fit_document("Короткий текст.", limit=100) == "Короткий текст."


def test_long_document_keeps_beginning_and_resolutive_part() -> None:
    from second_opinion.fact_extraction.llm_extractor import (
        MAX_DOCUMENT_CHARS,
        OMISSION_MARK,
        fit_document,
    )

    head = "НАЧАЛО приговора. "
    body = "Описание обстоятельств дела. " * 2000
    tail = "ПРИГОВОРИЛ: назначить наказание в виде лишения свободы сроком три года."
    text = head + body + tail

    fitted = fit_document(text)

    assert len(fitted) <= MAX_DOCUMENT_CHARS
    assert fitted.startswith(head)
    assert fitted.endswith(tail)
    assert OMISSION_MARK in fitted


def test_long_document_without_marker_keeps_the_end() -> None:
    from second_opinion.fact_extraction.llm_extractor import fit_document

    text = "а" * 50000 + "КОНЕЦ"
    fitted = fit_document(text, limit=20000)

    assert len(fitted) <= 20000
    assert fitted.endswith("КОНЕЦ")


def test_quote_from_resolutive_part_of_long_document_is_accepted() -> None:
    quote = "назначить наказание в виде лишения свободы сроком три года"
    text = "Описание дела. " * 3000 + "ПРИГОВОРИЛ: " + quote + "."
    payload = {"facts": [{"type": "punishment_type", "value": "imprisonment", "confidence": 0.9, "quote": quote}]}
    facts = LLMFactExtractor(_provider(payload)).extract(_document(text), known_keys=set())
    assert [f.type for f in facts] == [FactType.PUNISHMENT_TYPE]


def test_quote_spanning_the_omission_is_rejected() -> None:
    from second_opinion.fact_extraction.llm_extractor import OMISSION_MARK

    text = "Описание дела. " * 3000 + "ПРИГОВОРИЛ: назначить наказание."
    payload = {"facts": [{"type": "health_factor", "value": "x", "confidence": 0.9, "quote": "дела." + OMISSION_MARK}]}
    assert LLMFactExtractor(_provider(payload)).extract(_document(text), known_keys=set()) == []


def test_completed_stage_from_model_is_dropped_but_attempt_is_kept() -> None:
    text = "Подсудимый совершил преступление, а затем покушался на второе."
    payload = {
        "facts": [
            _fact("offense_stage", "completed", "совершил преступление"),
            _fact("offense_stage", "attempt", "покушался на второе"),
        ]
    }
    facts = LLMFactExtractor(_provider(payload)).extract(_document(text), known_keys=set())
    assert [(f.type, f.value) for f in facts] == [(FactType.OFFENSE_STAGE, "attempt")]


def test_stage_claim_without_stage_word_in_quote_is_dropped() -> None:
    text = "Подсудимая совершила преступление 19 октября 2025 года."
    payload = {"facts": [_fact("offense_stage", "attempt", "совершила преступление 19 октября 2025 года")]}
    assert LLMFactExtractor(_provider(payload)).extract(_document(text), known_keys=set()) == []


def test_preparation_claim_with_stage_word_is_kept() -> None:
    text = "Подсудимый совершил приготовление к краже."
    payload = {"facts": [_fact("offense_stage", "preparation", "совершил приготовление к краже")]}
    facts = LLMFactExtractor(_provider(payload)).extract(_document(text), known_keys=set())
    assert [(f.type, f.value) for f in facts] == [(FactType.OFFENSE_STAGE, "preparation")]

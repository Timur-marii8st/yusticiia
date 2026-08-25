from __future__ import annotations

import json

from second_opinion.domain.documents import Document
from second_opinion.domain.enums import FactStatus, FactType
from second_opinion.fact_extraction.llm_extractor import LLMFactExtractor
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
    assert fact.prompt_version == "v1"


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

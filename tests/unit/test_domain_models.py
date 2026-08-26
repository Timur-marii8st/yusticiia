from __future__ import annotations

import pytest
from pydantic import ValidationError

from second_opinion.domain import (
    Evidence,
    LegalFact,
    NormVersion,
)
from second_opinion.domain.enums import FactStatus, FactType


def test_fact_without_evidence_cannot_be_verified() -> None:
    with pytest.raises(ValidationError):
        LegalFact(
            id="f1",
            type=FactType.GUILTY_PLEA,
            value=True,
            confidence=1.0,
            evidence=[],
            extraction_method="pattern",
            status=FactStatus.VERIFIED,
        )


def test_fact_without_evidence_can_be_not_found() -> None:
    fact = LegalFact(
        id="f1",
        type=FactType.GUILTY_PLEA,
        value=None,
        confidence=1.0,
        evidence=[],
        extraction_method="pattern",
        status=FactStatus.NOT_FOUND,
    )
    assert fact.status is FactStatus.NOT_FOUND


def test_fact_with_evidence_can_be_verified() -> None:
    fact = LegalFact(
        id="f1",
        type=FactType.GUILTY_PLEA,
        value=True,
        confidence=0.95,
        evidence=[
            Evidence(
                document_id="d1", quote="вину признал", start_offset=10, end_offset=22
            )
        ],
        extraction_method="pattern",
        status=FactStatus.VERIFIED,
    )
    assert fact.status is FactStatus.VERIFIED


def test_evidence_offsets_must_be_coherent() -> None:
    with pytest.raises(ValidationError):
        Evidence(document_id="d1", quote="abc", start_offset=10, end_offset=10)


def test_norm_version_checksum_must_match_text() -> None:
    with pytest.raises(ValidationError):
        NormVersion(
            norm_id="uk-rf:art-158-part-2",
            version_id="v1",
            text="текст нормы",
            effective_from="2020-01-01",
            effective_to=None,
            source_document="тест",
            source_url="",
            retrieved_at="2026-01-01",
            sha256="0" * 64,
        )


def test_norm_version_valid_checksum() -> None:
    text = "текст нормы"
    version = NormVersion(
        norm_id="uk-rf:art-158-part-2",
        version_id="v1",
        text=text,
        effective_from="2020-01-01",
        effective_to=None,
        source_document="тест",
        source_url="",
        retrieved_at="2026-01-01",
        sha256=NormVersion.compute_sha256(text),
    )
    assert version.sha256 == NormVersion.compute_sha256(text)


def test_norm_version_rejects_reversed_dates() -> None:
    text = "текст"
    with pytest.raises(ValidationError):
        NormVersion(
            norm_id="uk-rf:art-158-part-2",
            version_id="v1",
            text=text,
            effective_from="2022-01-01",
            effective_to="2020-01-01",
            source_document="тест",
            source_url="",
            retrieved_at="2026-01-01",
            sha256=NormVersion.compute_sha256(text),
        )


# -- сборка CaseFacts: приоритет форм группового деяния ---------------------------


def _group_fact(value: str) -> LegalFact:
    from second_opinion.domain.enums import ExtractionMethod
    from second_opinion.domain.evidence import Evidence

    return LegalFact(
        id=f"fact-group-{abs(hash(value)) % 10000}",
        type=FactType.GROUP_OFFENSE,
        value=value,
        confidence=0.95,
        evidence=[Evidence(document_id="t", quote="q", start_offset=0, end_offset=1)],
        extraction_method=ExtractionMethod.PATTERN,
        status=FactStatus.VERIFIED,
    )


def test_builder_keeps_strongest_group_role() -> None:
    from second_opinion.fact_extraction.builder import build_case_facts

    case = build_case_facts(
        [
            _group_fact("group_with_conspiracy"),
            _group_fact("group_of_persons"),
            _group_fact("organized_group"),
        ]
    )
    assert case.offense.complicity_role == "organized_group"

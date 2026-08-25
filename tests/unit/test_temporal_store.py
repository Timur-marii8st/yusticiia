from __future__ import annotations

from datetime import date

import pytest

from second_opinion.domain.norms import LegalNorm, NormVersion
from second_opinion.legal_sources.store import (
    NoApplicableVersionError,
    NormNotFoundError,
    NormStore,
)

SYNTHETIC_NORM = "uk-rf:art-999-synthetic"


def test_selects_older_version_for_historical_date(norm_store: NormStore) -> None:
    version = norm_store.get_norm(SYNTHETIC_NORM, date(2022, 6, 1))
    assert version.version_id == "v2020"
    sanction = version.sanctions[0]
    assert sanction.max_months == 36


def test_selects_current_version_for_recent_date(norm_store: NormStore) -> None:
    version = norm_store.get_norm(SYNTHETIC_NORM, date(2025, 1, 15))
    assert version.version_id == "v2024"
    assert version.sanctions[0].max_months == 60


def test_boundary_dates_resolve_correctly(norm_store: NormStore) -> None:
    assert norm_store.get_norm(SYNTHETIC_NORM, date(2023, 12, 31)).version_id == "v2020"
    assert norm_store.get_norm(SYNTHETIC_NORM, date(2024, 1, 1)).version_id == "v2024"


def test_no_version_before_effective_range(norm_store: NormStore) -> None:
    with pytest.raises(NoApplicableVersionError):
        norm_store.get_norm(SYNTHETIC_NORM, date(2019, 1, 1))


def test_unknown_norm_raises(norm_store: NormStore) -> None:
    with pytest.raises(NormNotFoundError):
        norm_store.get_norm("uk-rf:art-500", date(2025, 1, 1))


def test_find_by_article(norm_store: NormStore) -> None:
    norm = norm_store.find_by_article("УК РФ", 158, 2)
    assert norm is not None
    assert norm.norm_id == "uk-rf:art-158-part-2"
    assert norm_store.find_by_article("УК РФ", 158, 5) is None


def test_checksum_matches_loaded_text(norm_store: NormStore) -> None:
    version = norm_store.get_norm(SYNTHETIC_NORM, date(2025, 1, 1))
    assert version.sha256 == NormVersion.compute_sha256(version.text)


def test_overlapping_versions_rejected() -> None:
    store = NormStore()
    text_a = "редакция A"
    text_b = "редакция B"
    versions = [
        NormVersion(
            norm_id="test:art-1",
            version_id="a",
            text=text_a,
            effective_from=date(2020, 1, 1),
            effective_to=date(2023, 12, 31),
            source_document="тест",
            source_url="",
            retrieved_at=date(2026, 1, 1),
            sha256=NormVersion.compute_sha256(text_a),
        ),
        NormVersion(
            norm_id="test:art-1",
            version_id="b",
            text=text_b,
            effective_from=date(2023, 12, 31),  # пересекается с предыдущей
            effective_to=None,
            source_document="тест",
            source_url="",
            retrieved_at=date(2026, 1, 1),
            sha256=NormVersion.compute_sha256(text_b),
        ),
    ]
    norm = LegalNorm(norm_id="test:art-1", code="УК РФ", article=1, part=None, title="тест")
    with pytest.raises(ValueError):
        store.add_norm(norm, versions)

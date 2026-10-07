"""Нормы в PostgreSQL: тот же контракт, что у файлового носителя.

Обновление нормы в базе не требует выпуска релиза — это и есть причина
существования второго носителя (ТЗ: Legal Database -> versioning).
"""

from __future__ import annotations

import os
import uuid
from datetime import date

import pytest

from second_opinion.config import PROJECT_ROOT
from second_opinion.legal_sources.store import (
    NoApplicableVersionError,
    NormNotFoundError,
    NormStore,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("SO_TEST_DATABASE_URL"),
    reason="SO_TEST_DATABASE_URL не задан — пропускаем postgres-тесты",
)

FIXTURES = PROJECT_ROOT / "data" / "fixtures" / "norms"


@pytest.fixture()
def pg_store():
    from second_opinion.legal_sources.postgres_store import PostgresNormStore

    return PostgresNormStore(
        os.environ["SO_TEST_DATABASE_URL"], table_prefix=f"t{uuid.uuid4().hex[:8]}_"
    )


@pytest.fixture()
def file_store() -> NormStore:
    return NormStore.from_directory(FIXTURES)


def test_import_preserves_every_norm(pg_store, file_store) -> None:
    imported = pg_store.sync_from(file_store)
    assert imported == len(file_store.list_norms())
    assert {n.norm_id for n in pg_store.list_norms()} == {
        n.norm_id for n in file_store.list_norms()
    }


def test_redaction_on_date_matches_file_store(pg_store, file_store) -> None:
    """Главный запрос системы обязан давать тот же ответ на обоих носителях."""
    pg_store.sync_from(file_store)

    for norm in file_store.list_norms():
        for version in file_store.versions(norm.norm_id):
            probe = version.effective_from
            assert (
                pg_store.get_norm(norm.norm_id, probe).version_id
                == file_store.get_norm(norm.norm_id, probe).version_id
            ), f"{norm.norm_id} на {probe}"


def test_sanctions_survive_the_round_trip(pg_store, file_store) -> None:
    """Пределы наказания читает движок правил — потерять их нельзя."""
    pg_store.sync_from(file_store)
    source = file_store.get_norm("uk-rf:art-158-part-1", date(2010, 1, 1))
    stored = pg_store.get_norm("uk-rf:art-158-part-1", date(2010, 1, 1))
    assert [s.model_dump() for s in stored.sanctions] == [
        s.model_dump() for s in source.sanctions
    ]
    assert stored.sha256 == source.sha256


def test_unknown_norm_raises_same_error(pg_store) -> None:
    with pytest.raises(NormNotFoundError):
        pg_store.get_norm_meta("нет-такой")


def test_date_outside_coverage_raises_same_error(pg_store, file_store) -> None:
    pg_store.sync_from(file_store)
    with pytest.raises(NoApplicableVersionError):
        pg_store.get_norm("uk-rf:art-999-synthetic", date(1990, 1, 1))


def test_overlapping_intervals_are_refused(pg_store) -> None:
    """Инварианты не зависят от носителя."""
    from second_opinion.domain.norms import LegalNorm, NormVersion

    norm = LegalNorm(norm_id="t:1", code="УК РФ", article=1, part=1, title="Т")
    def _version(vid: str, start: date, end: date | None) -> NormVersion:
        text = f"текст {vid}"
        return NormVersion(
            norm_id="t:1", version_id=vid, text=text, effective_from=start,
            effective_to=end, source_document="и", retrieved_at=date(2026, 1, 1),
            sha256=NormVersion.compute_sha256(text),
        )

    with pytest.raises(ValueError, match="пересекаются"):
        pg_store.add_norm(norm, [_version("a", date(2000, 1, 1), None),
                                 _version("b", date(2005, 1, 1), None)])


def test_updating_a_norm_needs_no_release(pg_store, file_store) -> None:
    """Смысл носителя: новая редакция видна сразу, без перезапуска."""
    from second_opinion.domain.norms import NormVersion

    pg_store.sync_from(file_store)
    norm_id = "uk-rf:art-228-part-1"
    existing = pg_store.versions(norm_id)
    text = "Ч. 1 ст. 228 УК РФ в новой редакции."
    updated = [
        *[v.model_copy(update={"effective_to": date(2023, 12, 31)})
          for v in existing if v.effective_to is None],
        *[v for v in existing if v.effective_to is not None],
        NormVersion(
            norm_id=norm_id, version_id="v-2024", text=text,
            effective_from=date(2024, 1, 1), source_document="и",
            retrieved_at=date(2026, 1, 1), sha256=NormVersion.compute_sha256(text),
        ),
    ]
    pg_store.add_norm(pg_store.get_norm_meta(norm_id), updated)

    assert pg_store.get_norm(norm_id, date(2025, 1, 1)).version_id == "v-2024"

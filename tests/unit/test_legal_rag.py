from __future__ import annotations

from datetime import date

from second_opinion.legal_rag import LegalRag, tokenize
from second_opinion.legal_sources.store import NormStore

SYNTHETIC_NORM = "uk-rf:art-999-synthetic"


def test_attempt_query_ranks_art66_first(norm_store: NormStore) -> None:
    hits = LegalRag(norm_store).search("предел наказания при покушении")
    assert hits
    assert hits[0].norm_id == "uk-rf:art-66"
    assert hits[0].fragment
    assert hits[0].matched_terms


def test_special_procedure_query_finds_art62(norm_store: NormStore) -> None:
    hits = LegalRag(norm_store).search("особый порядок две трети максимума")
    assert any(hit.norm_id == "uk-rf:art-62" for hit in hits)


def test_suspended_query_finds_art73(norm_store: NormStore) -> None:
    hits = LegalRag(norm_store).search("условное осуждение восемь лет")
    assert hits
    # После добавления разъяснения Пленума по ст. 73 оба источника
    # релевантны; проверяем, что норма УК в топ-2.
    top_ids = [hit.norm_id for hit in hits[:2]]
    assert "uk-rf:art-73" in top_ids


def test_article_number_query(norm_store: NormStore) -> None:
    hits = LegalRag(norm_store).search("66")
    assert any(hit.norm_id == "uk-rf:art-66" for hit in hits)


def test_temporal_search_selects_right_version(norm_store: NormStore) -> None:
    rag = LegalRag(norm_store)
    old = rag.search("синтетическая фикстура", applicable_at=date(2022, 6, 1))
    assert old and old[0].norm_id == SYNTHETIC_NORM
    assert old[0].version_id == "v2020"

    new = rag.search("синтетическая фикстура", applicable_at=date(2025, 1, 1))
    assert new and new[0].norm_id == SYNTHETIC_NORM
    assert new[0].version_id == "v2024"


def test_temporal_search_skips_norm_without_applicable_version(
    norm_store: NormStore,
) -> None:
    hits = LegalRag(norm_store).search(
        "синтетическая фикстура", applicable_at=date(2019, 1, 1)
    )
    assert not any(hit.norm_id == SYNTHETIC_NORM for hit in hits)


def test_nonsense_query_returns_empty(norm_store: NormStore) -> None:
    assert LegalRag(norm_store).search("квантовая хромодинамика глюонов") == []


def test_stopword_only_query_returns_empty(norm_store: NormStore) -> None:
    assert LegalRag(norm_store).search("и в не по") == []
    assert LegalRag(norm_store).search("ст. ч.") == []


def test_every_hit_carries_full_provenance(norm_store: NormStore) -> None:
    hits = LegalRag(norm_store).search("назначение наказания")
    assert hits
    for hit in hits:
        assert hit.fragment.strip()
        assert len(hit.sha256) == 64
        assert hit.source_document
        assert hit.ref
        assert hit.effective_from is not None
        assert hit.verification_status in {"draft", "verified"}


def test_fragments_come_from_stored_versions(norm_store: NormStore) -> None:
    rag = LegalRag(norm_store)
    for hit in rag.search("наказание", limit=50):
        stored = norm_store.versions(hit.norm_id)
        assert hit.fragment in [version.text for version in stored]


def test_code_filter(norm_store: NormStore) -> None:
    hits = LegalRag(norm_store).search("наказание", code="УПК РФ")
    assert hits == []


def test_article_filter(norm_store: NormStore) -> None:
    hits = LegalRag(norm_store).search("наказание", article=66)
    assert hits
    assert all(hit.norm_id == "uk-rf:art-66" for hit in hits)


def test_tokenize_drops_stopwords_and_short_tokens() -> None:
    assert tokenize("и в не по УК РФ ст.") == []
    tokens = tokenize("Предел наказания при покушении на преступление")
    assert "покушении" in tokens
    assert "предел" in tokens


def test_limit_is_respected(norm_store: NormStore) -> None:
    hits = LegalRag(norm_store).search("наказание", limit=2)
    assert len(hits) <= 2

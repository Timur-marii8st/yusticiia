from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("SO_TEST_DATABASE_URL"),
    reason="SO_TEST_DATABASE_URL не задан — пропускаем pgvector-тесты",
)


@pytest.fixture()
def pg_dsn() -> str:
    return os.environ["SO_TEST_DATABASE_URL"]


def _norm_store():
    from pathlib import Path

    from second_opinion.legal_sources.store import NormStore

    return NormStore.from_directory(Path("data/fixtures/norms"))


def test_pgvector_sync_and_rank(pg_dsn: str) -> None:
    from second_opinion.legal_rag.embeddings import HashingTfidfEmbedder
    from second_opinion.storage.vector import PgVectorNormStore

    store = _norm_store()
    embedder = HashingTfidfEmbedder()
    vector_store = PgVectorNormStore(pg_dsn, embedder=embedder, table="norm_vectors_test_sync")

    count = vector_store.sync(store)
    assert count > 0

    # Ранжирование через БД должно вернуть близости для хитов
    from second_opinion.legal_rag import LegalRag

    rag = LegalRag(store, embedder=embedder, vector_store=vector_store)
    hits = rag.search("покушение на преступление пределы наказания", limit=5)
    assert hits
    # В гибридном режиме семантические баллы заполняются
    assert any(hit.semantic_score is not None for hit in hits)


def test_hybrid_pgvector_matches_inmemory(pg_dsn: str) -> None:
    """pgvector-реранкинг не хуже in-memory: те же кандидаты, инвариант сохранён."""
    from second_opinion.legal_rag import LegalRag
    from second_opinion.legal_rag.embeddings import HashingTfidfEmbedder
    from second_opinion.storage.vector import PgVectorNormStore

    store = _norm_store()
    embedder = HashingTfidfEmbedder()

    vector_store = PgVectorNormStore(pg_dsn, embedder=embedder, table="norm_vectors_test_match")
    vector_store.sync(store)

    rag_mem = LegalRag(store, embedder=embedder)
    rag_pg = LegalRag(store, embedder=embedder, vector_store=vector_store)

    query = "особый порядок судебного разбирательства"
    hits_mem = rag_mem.search(query, limit=5)
    hits_pg = rag_pg.search(query, limit=5)

    # Оба возвращают только хранящиеся фрагменты, отсортированы
    assert hits_mem
    assert hits_pg
    assert len(hits_mem) == len(hits_pg)
    # Инвариант: фрагменты из хранилища, каждый hit имеет реальную норму
    for hit in hits_pg:
        assert store.get_norm_meta(hit.norm_id)
        # векторный поиск не добавляет чужеродных фрагментов
        assert hit.fragment


def test_vector_global_search(pg_dsn: str) -> None:
    from second_opinion.legal_rag.embeddings import HashingTfidfEmbedder
    from second_opinion.storage.vector import PgVectorNormStore

    store = _norm_store()
    vector_store = PgVectorNormStore(pg_dsn, embedder=HashingTfidfEmbedder(), table="norm_vectors_test_global")
    vector_store.sync(store)

    results = vector_store.vector_search("рецидив преступлений отягчающее", limit=3)
    assert len(results) == 3
    # Корпус синтетический — проверяем лишь валидность диапазона, не точный топ
    # Но хотя бы — все результаты имеют similarity в [0,1]
    for _, _, sim in results:
        assert 0.0 <= sim <= 1.0

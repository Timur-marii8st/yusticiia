from __future__ import annotations

import pytest

from second_opinion.legal_rag import (
    HashingTfidfEmbedder,
    LegalRag,
    OpenAICompatibleEmbeddingProvider,
)
from second_opinion.legal_rag.embeddings import cosine


@pytest.fixture()
def hybrid_rag(norm_store) -> LegalRag:
    return LegalRag(norm_store, embedder=HashingTfidfEmbedder())


# -- HashingTfidfEmbedder ------------------------------------------------------


def test_embedding_deterministic() -> None:
    embedder = HashingTfidfEmbedder()
    first = embedder.embed(["покушение на преступление"])
    second = embedder.embed(["покушение на преступление"])
    assert first[0] == second[0]


def test_embedding_l2_normalized() -> None:
    vector = HashingTfidfEmbedder().embed(["условное осуждение"])[0]
    norm = sum(v * v for v in vector) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_cosine_self_similarity_is_one() -> None:
    embedder = HashingTfidfEmbedder()
    vectors = embedder.embed(["одинаковый текст", "одинаковый текст"])
    assert abs(cosine(vectors[0], vectors[1]) - 1.0) < 1e-9


def test_cosine_of_empty_vectors_is_zero() -> None:
    assert cosine([], []) == 0.0


def test_semantic_ordering_by_topic() -> None:
    """Вектор близости должен разводить темы без нейронной модели."""
    embedder = HashingTfidfEmbedder()
    query = embedder.embed(["пределы наказания при покушении на преступление"])[0]
    stage_text, theft_text = embedder.embed(
        [
            "за приготовление не более половины максимума санкции за покушение три четверти",
            "кража чужого имущества лишение свободы до двух лет",
        ]
    )
    assert cosine(query, stage_text) > cosine(query, theft_text)


# -- Гибридный реранкинг --------------------------------------------------------


def test_hybrid_returns_stored_fragments_only(hybrid_rag: LegalRag) -> None:
    hits = hybrid_rag.search("особый порядок назначение наказания", limit=10)
    assert hits
    for hit in hits:
        # Фрагмент обязан быть текстом хранящейся редакции.
        versions = {v.version_id for v in hybrid_rag._store.versions(hit.norm_id)}
        assert hit.version_id in versions


def test_hybrid_keeps_relevant_norm_on_top(hybrid_rag: LegalRag) -> None:
    hits = hybrid_rag.search(
        "пределы назначения наказания за неоконченное преступление покушение",
        limit=5,
    )
    assert hits[0].norm_id == "uk-rf:art-66"


def test_hybrid_populates_component_scores(hybrid_rag: LegalRag) -> None:
    hits = hybrid_rag.search("рецидив преступлений", limit=3)
    for hit in hits:
        assert hit.lexical_score is not None
        assert hit.semantic_score is not None


def test_lexical_mode_has_no_semantic_fields(norm_store) -> None:
    hits = LegalRag(norm_store).search("рецидив", limit=3)
    assert hits
    for hit in hits:
        assert hit.lexical_score is None
        assert hit.semantic_score is None


def test_hybrid_empty_query_returns_empty(hybrid_rag: LegalRag) -> None:
    assert hybrid_rag.search("ук рф статья") == []


def test_hybrid_no_results_for_absent_topic(hybrid_rag: LegalRag) -> None:
    assert hybrid_rag.search("взятка должностному лицу") == []


def test_hybrid_respects_limit(hybrid_rag: LegalRag) -> None:
    hits = hybrid_rag.search("назначение наказания судом с учётом обстоятельств", limit=2)
    assert len(hits) <= 2


# -- Внешний провайдер эмбеддингов ----------------------------------------------


def test_openai_embedder_requires_key() -> None:
    with pytest.raises(ValueError, match="явной настройкой"):
        OpenAICompatibleEmbeddingProvider("http://localhost/v1", "", "m")

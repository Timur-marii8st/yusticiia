from __future__ import annotations

import math
import re
from collections.abc import Callable
from datetime import date

from pydantic import BaseModel, Field

from ..domain.norms import LegalNorm, NormVersion
from ..legal_sources.store import NoApplicableVersionError, NormStore
from .embeddings import EmbeddingProvider, cosine

_TOKEN = re.compile(r"[a-zа-яё0-9]+")

#: Константа Reciprocal Rank Fusion: чем больше, тем ровнее вклад позиций.
RRF_K = 60

#: Слова, не несущие юридического смысла в запросе. «ук/рф/упк/статья/часть»
#: совпали бы с каждой нормой и только размывали ранжирование.
_STOPWORDS = {
    "и", "в", "во", "не", "на", "по", "для", "при", "это", "как", "или",
    "если", "то", "из", "за", "от", "об", "о", "к", "со", "что", "его",
    "её", "их", "быть", "есть", "также", "либо", "когда", "который",
    "ук", "рф", "упк", "коап", "ст", "статья", "статьи", "часть", "части",
    "кодекса", "кодекс",
}

#: Длина префикса для грубого учёта словоформ (русская флексия).
_PREFIX_LEN = 6


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in _TOKEN.findall(text.lower()):
        if raw in _STOPWORDS:
            continue
        if raw.isdigit() or len(raw) >= 3:
            tokens.append(raw)
    # Убираем дубликаты, сохраняя порядок.
    seen: set[str] = set()
    unique: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            unique.append(token)
    return unique


def _prefix(token: str) -> str:
    return token[:_PREFIX_LEN] if len(token) > _PREFIX_LEN else token


def _haystack(norm: LegalNorm, version: NormVersion) -> str:
    return f"{norm.title} {norm.ref} {version.text}"


class SearchHit(BaseModel):
    """Результат поиска: всегда несёт фрагмент реального источника."""

    norm_id: str
    ref: str
    title: str
    version_id: str
    fragment: str
    effective_from: date
    effective_to: date | None = None
    source_document: str
    source_url: str = ""
    retrieved_at: date
    sha256: str
    verification_status: str
    synthetic: bool
    score: float
    matched_terms: list[str] = Field(default_factory=list)
    #: Лексический балл (в гибридном режиме отделяется от итогового).
    lexical_score: float | None = None
    #: Косинусная близость к запросу (только в гибридном режиме).
    semantic_score: float | None = None


class LegalRag:
    """Лексический поиск по базе источников с опциональным гибридным
    реранкингом (детерминированный; LLM не используется).

    Инвариант: результат содержит только реально хранящиеся фрагменты
    текста норм; если ничего не найдено — возвращается пустой список,
    а не сгенерированный ответ (см. docs/LEGAL_SAFETY_PRINCIPLES.md §6).
    Реранкинг меняет только ПОРЯДОК кандидатов, найденных лексической
    фазой, и никогда не добавляет новые фрагменты.
    """

    def __init__(
        self,
        norm_store: NormStore,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self._store = norm_store
        self._embedder = embedder

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        code: str | None = None,
        article: int | None = None,
        applicable_at: date | None = None,
    ) -> list[SearchHit]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        selected: list[tuple[LegalNorm, NormVersion]] = []
        for norm in self._store.list_norms():
            if code is not None and norm.code != code:
                continue
            if article is not None and norm.article != article:
                continue
            version = self._select_version(norm, applicable_at)
            if version is not None:
                selected.append((norm, version))
        if not selected:
            return []

        # IDF по выбранному корпусу редакций: редкие термины (например,
        # «покушение») весят больше частых («наказание»).
        term_df: dict[str, int] = {}
        hay_token_sets: dict[str, set[str]] = {}
        for norm, version in selected:
            tokens = set(tokenize(_haystack(norm, version)))
            hay_token_sets[norm.norm_id] = tokens
            for token in tokens:
                term_df[token] = term_df.get(token, 0) + 1
        corpus_size = len(selected)

        def idf(token: str) -> float:
            df = term_df.get(token, 0)
            if df == 0:
                prefix = _prefix(token)
                df = sum(
                    1
                    for tokens in hay_token_sets.values()
                    if any(_prefix(t) == prefix for t in tokens)
                )
            return math.log((corpus_size + 1) / (df + 1)) + 1.0

        query_weight = sum(idf(token) for token in query_tokens)

        hits: list[SearchHit] = []
        for norm, version in selected:
            score, matched = self._score(
                norm, version, query_tokens, idf, query_weight
            )
            if score <= 0:
                continue
            hits.append(
                SearchHit(
                    norm_id=norm.norm_id,
                    ref=norm.ref,
                    title=norm.title,
                    version_id=version.version_id,
                    fragment=version.text,
                    effective_from=version.effective_from,
                    effective_to=version.effective_to,
                    source_document=version.source_document,
                    source_url=version.source_url,
                    retrieved_at=version.retrieved_at,
                    sha256=version.sha256,
                    verification_status=version.verification_status.value,
                    synthetic=version.synthetic,
                    score=round(score, 3),
                    matched_terms=matched,
                )
            )
        hits.sort(key=lambda hit: (-hit.score, hit.norm_id))
        if self._embedder is None:
            return hits[:limit]
        return self._rerank(hits, query, limit)

    # -- внутреннее -----------------------------------------------------------

    def _rerank(
        self, hits: list[SearchHit], query: str, limit: int
    ) -> list[SearchHit]:
        """Гибридный реранкинг: Reciprocal Rank Fusion лексического и
        семантического ранжирований (ADR-003)."""
        query_vector = self._embedder.embed([query])[0]
        texts = [f"{hit.title} {hit.ref} {hit.fragment}" for hit in hits]
        vectors = self._embedder.embed(texts)
        similarities = [
            cosine(query_vector, vector) for vector in vectors
        ]

        lexical_ranking = sorted(
            range(len(hits)), key=lambda i: (-hits[i].score, hits[i].norm_id)
        )
        semantic_ranking = sorted(
            range(len(hits)), key=lambda i: (-similarities[i], hits[i].norm_id)
        )

        fused: dict[int, float] = {}
        for ranking in (lexical_ranking, semantic_ranking):
            for position, index in enumerate(ranking, start=1):
                fused[index] = fused.get(index, 0.0) + 1.0 / (RRF_K + position)

        reranked: list[SearchHit] = []
        for index, hit in enumerate(hits):
            reranked.append(
                hit.model_copy(
                    update={
                        "score": round(fused[index], 4),
                        "lexical_score": hit.score,
                        "semantic_score": round(similarities[index], 4),
                    }
                )
            )
        reranked.sort(key=lambda hit: (-hit.score, hit.norm_id))
        return reranked[:limit]

    def _select_version(
        self, norm: LegalNorm, applicable_at: date | None
    ) -> NormVersion | None:
        if applicable_at is not None:
            try:
                return self._store.get_norm(norm.norm_id, applicable_at)
            except NoApplicableVersionError:
                return None
        versions = self._store.versions(norm.norm_id)
        current = [v for v in versions if v.effective_to is None]
        return current[0] if current else versions[-1]

    @staticmethod
    def _score(
        norm: LegalNorm,
        version: NormVersion,
        query_tokens: list[str],
        idf: Callable[[str], float],
        query_weight: float,
    ) -> tuple[float, list[str]]:
        hay_tokens = tokenize(_haystack(norm, version))
        hay_exact = set(hay_tokens)
        hay_prefixes = {_prefix(token) for token in hay_tokens}
        title_exact = set(tokenize(norm.title)) | set(tokenize(norm.ref))

        score = 0.0
        matched: list[str] = []
        for token in query_tokens:
            weight = idf(token)
            if token in hay_exact:
                score += weight * (1.25 if token in title_exact else 1.0)
                matched.append(token)
            elif _prefix(token) in hay_prefixes:
                score += weight * 0.75
                matched.append(token)
        return score / query_weight, matched

from __future__ import annotations

import math
import re
from collections.abc import Callable
from datetime import date

from pydantic import BaseModel, Field

from ..domain.enums import VerificationStatus
from ..domain.norms import LegalNorm, NormVersion
from ..legal_sources.store import NoApplicableVersionError, NormStore
from .embeddings import EmbeddingProvider, cosine

_TOKEN = re.compile(r"[a-zа-яё0-9]+")

LEXICAL_WEIGHT = 0.7
BM25_K1 = 1.2
BM25_B = 0.75
DRAFT_WEIGHT = 0.85

_STOPWORDS = {
    "и", "в", "во", "не", "на", "по", "для", "при", "это", "как", "или",
    "если", "то", "из", "за", "от", "об", "о", "к", "со", "что", "его",
    "её", "их", "быть", "есть", "также", "либо", "когда", "который",
    "ук", "рф", "упк", "коап", "ст", "статья", "статьи", "часть", "части",
    "кодекса", "кодекс",
}

_PREFIX_LEN = 6


def tokenize(text: str) -> list[str]:
    """Слова запроса или текста: нижний регистр, «ё» как «е» (ИПС пишет «грабеж»)."""
    tokens: list[str] = []
    for raw in _TOKEN.findall(text.lower().replace("ё", "е")):
        if raw in _STOPWORDS:
            continue
        if raw.isdigit() or len(raw) >= 3:
            tokens.append(raw)
    seen: set[str] = set()
    unique: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            unique.append(token)
    return unique


def _prefix(token: str) -> str:
    return token[:_PREFIX_LEN] if len(token) > _PREFIX_LEN else token


def _min_max(values: list[float]) -> list[float]:
    if not values:
        return []
    low, high = min(values), max(values)
    if high <= low:
        return [1.0 for _ in values]
    return [(value - low) / (high - low) for value in values]


def _length_factor(length: int, average: float) -> float:
    """Поправка на длину текста по BM25 (k1 = 1,2, b = 0,75).

    Без неё длинный текст выигрывает потому, что длинный: в пункте Пленума на
    две тысячи знаков слова запроса встречаются чаще, чем в выверенной записи
    статьи на триста. Черновики к тому же получают вес ``DRAFT_WEIGHT``: при
    близкой релевантности судья сначала видит сверенный источник, черновик
    остаётся в выдаче со своим статусом.
    """
    if average <= 0:
        return 1.0
    return (BM25_K1 + 1) / (1 + BM25_K1 * (1 - BM25_B + BM25_B * length / average))


def haystack(norm: LegalNorm, version: NormVersion) -> str:
    """Текст, по которому ищется редакция: название, ``search_text`` нормы, ссылка, текст."""
    return " ".join(part for part in (norm.title, norm.search_text, norm.ref, version.text) if part)


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
    lexical_score: float | None = None
    semantic_score: float | None = None


class LegalRag:
    """Лексический поиск по базе источников с опциональным гибридным
    реранкингом (детерминированный; LLM не используется).

    Инвариант: результат содержит только реально хранящиеся фрагменты
    текста норм; если ничего не найдено — возвращается пустой список,
    а не сгенерированный ответ. Реранкинг меняет только ПОРЯДОК
    кандидатов, найденных лексической фазой, и никогда не добавляет
    новые фрагменты.
    """

    def __init__(
        self,
        norm_store: NormStore,
        embedder: EmbeddingProvider | None = None,
        vector_store=None,
    ) -> None:
        self._store = norm_store
        self._embedder = embedder
        self._vector_store = vector_store

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

        term_df: dict[str, int] = {}
        hay_token_sets: dict[str, set[str]] = {}
        doc_lengths: dict[str, int] = {}
        for norm, version in selected:
            all_tokens = tokenize(haystack(norm, version))
            doc_lengths[norm.norm_id] = len(all_tokens)
            tokens = set(all_tokens)
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
        average_length = sum(doc_lengths.values()) / len(doc_lengths)

        hits: list[SearchHit] = []
        for norm, version in selected:
            score, matched = self._score(
                norm, version, query_tokens, idf, query_weight
            )
            if score <= 0:
                continue
            score *= _length_factor(doc_lengths[norm.norm_id], average_length)
            if version.verification_status is VerificationStatus.DRAFT:
                score *= DRAFT_WEIGHT
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

    def _rerank(
        self, hits: list[SearchHit], query: str, limit: int
    ) -> list[SearchHit]:
        """Гибридный реранкинг: взвешенная сумма нормированных оценок.

        Лексическая и семантическая оценки приводятся к шкале 0..1 внутри
        выдачи и складываются с весами 0,7 и 0,3. Слияние по местам (RRF)
        стирало разрыв в оценках: норма с лексической оценкой 0,31 против
        0,11 у соседа получала почти тот же вклад, что второе место против
        пятого, и слабый семантический сигнал хэширующего векторизатора
        переставлял выдачу. Лексике вес больше, потому что она измерена как
        более точная на контрольном наборе (docs/EVALUATION.md).

        Если подключён pgvector-store, семантические близости берутся из БД
        (ANN-поиск), иначе — in-memory косинус.
        """
        similarities: list[float] | None = None
        if self._vector_store is not None:
            try:
                similarities = self._vector_store.rank(query, hits)
                if len(similarities) != len(hits):
                    similarities = None
            except Exception:  # noqa: BLE001
                similarities = None
        if similarities is None:
            assert self._embedder is not None
            query_vector = self._embedder.embed([query])[0]
            context = {norm.norm_id: norm.search_text for norm in self._store.list_norms()}
            texts = [
                " ".join(p for p in (hit.title, context.get(hit.norm_id, ""), hit.ref, hit.fragment) if p)
                for hit in hits
            ]
            vectors = self._embedder.embed(texts)
            similarities = [
                cosine(query_vector, vector) for vector in vectors
            ]

        weighted = [
            similarity * (DRAFT_WEIGHT if hits[i].verification_status == "draft" else 1.0)
            for i, similarity in enumerate(similarities)
        ]
        lexical = _min_max([hit.score for hit in hits])
        semantic = _min_max(weighted)
        fused = {
            index: LEXICAL_WEIGHT * lexical[index] + (1 - LEXICAL_WEIGHT) * semantic[index]
            for index in range(len(hits))
        }

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
        """Редакция на дату, а без даты — действующая.

        Норма без действующей редакции (часть статьи, которой в нынешнем тексте
        нет) без даты в выдачу не попадает: иначе текст 1996 года стоял бы в
        одном ряду с действующими как равноправный ответ.
        """
        if applicable_at is not None:
            try:
                return self._store.get_norm(norm.norm_id, applicable_at)
            except NoApplicableVersionError:
                return None
        current = [v for v in self._store.versions(norm.norm_id) if v.effective_to is None]
        return current[0] if current else None

    @staticmethod
    def _score(
        norm: LegalNorm,
        version: NormVersion,
        query_tokens: list[str],
        idf: Callable[[str], float],
        query_weight: float,
    ) -> tuple[float, list[str]]:
        hay_tokens = tokenize(haystack(norm, version))
        hay_exact = set(hay_tokens)
        hay_prefixes = {_prefix(token) for token in hay_tokens}
        title_exact = set(tokenize(norm.title)) | set(tokenize(norm.ref))

        score = 0.0
        matched: list[str] = []
        exact = 0
        for token in query_tokens:
            weight = idf(token)
            if token in hay_exact:
                score += weight * (1.25 if token in title_exact else 1.0)
                matched.append(token)
                exact += 1
            elif _prefix(token) in hay_prefixes:
                score += weight * 0.75
                matched.append(token)
        if exact == 0 and len(matched) == 1 and len(query_tokens) > 1:
            return 0.0, []
        return score / query_weight, matched

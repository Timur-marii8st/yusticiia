"""Провайдеры векторных представлений для реранкинга поиска.

Принципы (ADR-003, ADR-004):

- интерфейс ``EmbeddingProvider`` отделён от бизнес-логики;
- по умолчанию используется полностью офлайн детерминированный
  ``HashingTfidfEmbedder`` (символьные n-граммы + слова → хэш-вектор);
- внешний провайдер (OpenAI-совместимый /embeddings) включается только
  явно через переменные окружения; текст норм — не персональные данные,
  но решение об отправке остаётся за оператором.
"""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod

# Размерность хэш-пространства офлайн-эмбеддера. Компромисс: достаточно
# велика, чтобы развести n-граммы, и мала, чтобы считать быстро.
HASH_DIM = 1024

#: Диапазоны длин символьных n-грамм: устойчивость к русской флексии.
NGRAM_RANGE = (3, 5)


def _bucket(ngram: str) -> int:
    digest = hashlib.blake2b(ngram.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "little") % HASH_DIM


def cosine(a: list[float], b: list[float]) -> float:
    """Косинусная близость; нулевые векторы дают 0.0."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class EmbeddingProvider(ABC):
    """Интерфейс векторизатора текстов."""

    name: str

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Вернуть L2-нормированные векторы той же длины, что ``texts``."""


class HashingTfidfEmbedder(EmbeddingProvider):
    """Детерминированный офлайн-векторизатор.

    Характеристики: символьные n-граммы (3–5) + целые слова, хэширование
    в фиксированное пространство, сублинейный вес частоты (1+ln tf),
    L2-нормализация. Не требует моделей и сети; поведение воспроизводимо
    байт-в-байт на любой машине.
    """

    name = "hashing_tfidf"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        counts: dict[int, float] = {}
        normalized = " ".join(text.lower().split())
        for gram in self._ngrams(normalized):
            counts[_bucket(gram)] = counts.get(_bucket(gram), 0.0) + 1.0
        vector = [0.0] * HASH_DIM
        for bucket, tf in counts.items():
            vector[bucket] = 1.0 + math.log(tf)
        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 0:
            vector = [v / norm for v in vector]
        return vector

    @staticmethod
    def _ngrams(text: str) -> list[str]:
        grams: list[str] = []
        padded = f" {text} "
        low, high = NGRAM_RANGE
        for size in range(low, high + 1):
            if len(padded) < size:
                continue
            grams.extend(
                padded[i : i + size] for i in range(len(padded) - size + 1)
            )
        grams.extend(text.split())
        return grams


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Векторизация через внешний OpenAI-совместимый эндпоинт /embeddings.

    Включается ТОЛЬКО явной настройкой (см. docs/PRIVACY.md): тексты норм
    отправляются во внешнюю систему.
    """

    name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0) -> None:
        if not api_key:
            raise ValueError(
                "Ключ внешнего embedding-провайдера не задан: режим включается "
                "только явной настройкой окружения"
            )
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        response = httpx.post(
            f"{self._base_url}/embeddings",
            json={"model": self._model, "input": texts},
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = sorted(response.json()["data"], key=lambda item: item["index"])
        vectors = [item["embedding"] for item in data]
        if len(vectors) != len(texts):
            raise ValueError(
                f"провайдер вернул {len(vectors)} векторов для {len(texts)} текстов"
            )
        return vectors

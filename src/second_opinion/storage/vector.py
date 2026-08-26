"""pgvector-хранилище эмбеддингов норм для семантического ранжирования.

Используется только при ``SO_STORAGE_BACKEND=postgres`` совместно с
``SO_RAG_MODE=hybrid_pgvector`` (опционально). Демонстрирует хранение
векторов в PostgreSQL/pgvector и ANN-поиск, сохраняя гибридный инвариант:
реранкинг меняет порядок найденных кандидатов, не добавляя фрагментов.
"""

from __future__ import annotations

import threading

from ..legal_sources.store import NormStore


class PgVectorNormStore:
    """Векторное дополнение к NormStore (pgvector).

    Хранит L2-нормированные эмбеддинги заголовок+текст каждой редакции.
    Таблица создаётся лениво; расширение ``vector`` включается автоматически.
    """

    def __init__(
        self,
        dsn: str,
        embedder,  # EmbeddingProvider
        *,
        table: str = "norm_vectors",
        dimension: int = 1024,
    ) -> None:
        import psycopg  # noqa: F401

        self._dsn = dsn
        self._embedder = embedder
        self._table = table
        self._dim = dimension
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self):
        import psycopg

        return psycopg.connect(self._dsn)

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            # Регистрируем тип vector для psycopg (если установлен pgvector)
            try:
                from pgvector.psycopg import register_vector

                register_vector(conn)
            except Exception:  # noqa: BLE001
                pass
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {self._table} (  -- noqa: S608
                    norm_id text NOT NULL,
                    version_id text NOT NULL,
                    embedding vector({self._dim}) NOT NULL,
                    PRIMARY KEY (norm_id, version_id)
                )"""
            )
            # HNSW индекс для ANN-поиска (коснусная дистанция)
            conn.execute(
                f"""CREATE INDEX IF NOT EXISTS {self._table}_embedding_idx
                    ON {self._table} USING hnsw (embedding vector_cosine_ops)"""
            )

    def sync(self, norm_store: NormStore) -> int:
        """Синхронизировать все редакции норм в векторную таблицу.

        Возвращает число upsert-нутых записей.
        """
        # Собираем тексты для пакетной векторизации
        items: list[tuple[str, str, str]] = []  # (norm_id, version_id, haystack)
        for norm in norm_store.list_norms():
            for version in norm_store.versions(norm.norm_id):
                haystack = f"{norm.title} {norm.ref} {version.text}"
                items.append((norm.norm_id, version.version_id, haystack))
        if not items:
            return 0
        texts = [hay for _, _, hay in items]
        vectors = self._embedder.embed(texts)
        count = 0
        with self._lock, self._connect() as conn:
            try:
                from pgvector.psycopg import register_vector

                register_vector(conn)
            except Exception:  # noqa: BLE001
                pass
            for (norm_id, version_id, _), vector in zip(items, vectors, strict=False):
                conn.execute(
                    f"""INSERT INTO {self._table} (norm_id, version_id, embedding)
                        VALUES (%s, %s, %s)  -- noqa: S608
                        ON CONFLICT (norm_id, version_id)
                        DO UPDATE SET embedding = EXCLUDED.embedding""",
                    (norm_id, version_id, vector),
                )
                count += 1
        return count

    def rank(
        self,
        query: str,
        hits: list,  # list[SearchHit]
    ) -> list[float]:
        """Вернуть косинусные близости запроса к каждому хиту через pgvector.

        Порядок результата соответствует порядку ``hits``. При ошибке БД
        (например, расширение недоступно) — пустой список для fallback.
        """
        if not hits:
            return []
        query_vector = self._embedder.embed([query])[0]
        # Карта (norm_id, version_id) -> индекс хита
        index_map: dict[tuple[str, str], int] = {
            (hit.norm_id, hit.version_id): idx for idx, hit in enumerate(hits)
        }
        similarities: list[float] = [0.0] * len(hits)
        try:
            with self._connect() as conn:
                try:
                    from pgvector.psycopg import register_vector

                    register_vector(conn)
                except Exception:  # noqa: BLE001
                    pass
                # Запрашиваем близость для кандидатов одним запросом через IN
                # с использованием оператора <=> (cosine distance)
                norm_ids = list({hit.norm_id for hit in hits})
                version_ids = list({hit.version_id for hit in hits})
                # pgvector: 1 - cosine_distance = cosine_similarity (для L2-нормированных)
                rows = conn.execute(
                    f"""SELECT norm_id, version_id,
                               1 - (embedding <=> %s::vector) as similarity
                        FROM {self._table}
                        WHERE norm_id = ANY(%s) AND version_id = ANY(%s)""",
                    (query_vector, norm_ids, version_ids),
                ).fetchall()
                for norm_id, version_id, similarity in rows:
                    idx = index_map.get((norm_id, version_id))
                    if idx is not None:
                        similarities[idx] = float(similarity) if similarity is not None else 0.0
        except Exception:  # noqa: BLE001
            return []
        return similarities

    def vector_search(
        self,
        query: str,
        *,
        limit: int = 10,
        norm_ids: list[str] | None = None,
    ) -> list[tuple[str, str, float]]:
        """Глобальный ANN-поиск: ближайшие редакции к запросу.

        Возвращает (norm_id, version_id, similarity), отсортировано по убыванию.
        Используется для демонстрации, не в основном конвейере (там — rerank).
        """
        query_vector = self._embedder.embed([query])[0]
        with self._connect() as conn:
            try:
                from pgvector.psycopg import register_vector

                register_vector(conn)
            except Exception:  # noqa: BLE001
                pass
            if norm_ids is not None:
                rows = conn.execute(
                    f"""SELECT norm_id, version_id,
                               1 - (embedding <=> %s::vector) as similarity
                        FROM {self._table}
                        WHERE norm_id = ANY(%s)
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s""",
                    (query_vector, norm_ids, query_vector, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""SELECT norm_id, version_id,
                               1 - (embedding <=> %s::vector) as similarity
                        FROM {self._table}
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s""",
                    (query_vector, query_vector, limit),
                ).fetchall()
        return [(r[0], r[1], float(r[2])) for r in rows]

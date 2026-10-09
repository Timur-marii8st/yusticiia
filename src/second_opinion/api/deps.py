from __future__ import annotations

import json
import logging
from pathlib import Path

from ..audit.trail import AuditTrail
from ..config import AppConfig, load_config
from ..domain.analysis import AnalysisReport
from ..domain.cases import ComparableCase
from ..domain.documents import Document
from ..legal_rag.embeddings import EmbeddingProvider
from ..legal_sources.store import NormStore
from ..llm.mock_provider import MockLLMProvider
from ..llm.provider import LLMProvider
from ..pipeline import AnalysisPipeline
from ..retrieval.case_retrieval import CaseRetriever
from ..storage.repositories import JsonFileRepository, Repository

logger = logging.getLogger(__name__)


def load_cases(fixtures_dir: Path) -> list[ComparableCase]:
    cases: list[ComparableCase] = []
    cases_dir = fixtures_dir / "cases"
    if cases_dir.exists():
        for path in sorted(cases_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and "cases" in payload:
                cases.extend(ComparableCase.model_validate(item) for item in payload["cases"])
            elif isinstance(payload, list):
                cases.extend(ComparableCase.model_validate(item) for item in payload)
            else:
                cases.append(ComparableCase.model_validate(payload))
    return cases


def build_legal_rag(norm_store: NormStore):
    """Собрать поисковик по конфигурации: лексический, гибридный или
    гибридный c pgvector."""
    from ..config import load_config
    from ..legal_rag import (
        HashingTfidfEmbedder,
        LegalRag,
        OpenAICompatibleEmbeddingProvider,
    )

    config = load_config()
    if config.rag_mode not in ("hybrid", "hybrid_pgvector"):
        return LegalRag(norm_store)

    embedder: EmbeddingProvider
    if config.embeddings_provider == "openai_compatible":
        embedder = OpenAICompatibleEmbeddingProvider(
            config.openai_base_url, config.openai_api_key, config.embedding_model
        )
    else:
        embedder = HashingTfidfEmbedder()

    if config.rag_mode == "hybrid_pgvector":
        if config.storage_backend != "postgres" or not config.database_url:
            logger.warning(
                "SO_RAG_MODE=hybrid_pgvector, но носитель не настроен "
                "(SO_STORAGE_BACKEND=%s, SO_DATABASE_URL %s) — поиск идёт "
                "в режиме hybrid без векторного хранилища",
                config.storage_backend,
                "задан" if config.database_url else "не задан",
            )
            return LegalRag(norm_store, embedder=embedder)
        try:
            from ..storage.vector import PgVectorNormStore

            vector_store = PgVectorNormStore(
                config.database_url, embedder=embedder
            )
            vector_store.sync(norm_store)
            return LegalRag(
                norm_store, embedder=embedder, vector_store=vector_store
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pgvector недоступен (%s) — поиск переходит в режим hybrid "
                "без векторного хранилища: выдача будет отличаться от "
                "заявленного режима",
                exc,
            )
            return LegalRag(norm_store, embedder=embedder)

    return LegalRag(norm_store, embedder=embedder)


def build_norm_store(config: AppConfig):
    """Выбрать носитель норм по ``SO_NORMS_BACKEND``.

    Файловый — корпус под ревью в git, опечатанный манифестом. Postgres —
    обновление нормы без выпуска релиза; целостность там обеспечивают
    аудит-журнал и права доступа, а не контрольная сумма файла.
    """
    if config.norms_backend == "postgres":
        if not config.database_url:
            raise ValueError(
                "SO_DATABASE_URL не задан: norms_backend=postgres требует DSN"
            )
        from ..legal_sources.postgres_store import PostgresNormStore

        return PostgresNormStore(config.database_url)
    store = NormStore.from_directory(config.fixtures_dir / "norms")
    if not store.list_norms():
        logger.warning(
            "каталог норм пуст или не найден: %s (SO_FIXTURES_DIR): проверки будут отвечать "
            "«норма не найдена». В Docker смонтируйте каталог с fixtures/ или пустой том: "
            "образ скопирует нормы сам.",
            config.fixtures_dir / "norms",
        )
    return store


def build_pipeline(config: AppConfig | None = None) -> AnalysisPipeline:
    config = config or load_config()

    documents: Repository[Document]
    analyses: Repository[AnalysisReport]
    if config.storage_backend == "postgres":
        if not config.database_url:
            raise ValueError(
                "SO_DATABASE_URL не задан: storage_backend=postgres требует DSN"
            )
        from ..storage.postgres import PostgresJsonRepository

        documents = PostgresJsonRepository[Document](
            config.database_url,
            table="documents",
            model=Document,
            id_field="document_id",
        )
        analyses = PostgresJsonRepository[AnalysisReport](
            config.database_url,
            table="analyses",
            model=AnalysisReport,
            id_field="analysis_id",
        )
    else:
        config.store_dir.mkdir(parents=True, exist_ok=True)
        documents = JsonFileRepository[Document](
            config.store_dir / "documents", Document, "document_id"
        )
        analyses = JsonFileRepository[AnalysisReport](
            config.store_dir / "analyses", AnalysisReport, "analysis_id"
        )
    norm_store = build_norm_store(config)
    retriever = CaseRetriever(load_cases(config.fixtures_dir))

    provider: LLMProvider
    if config.llm_provider == "gigachat":
        from ..llm.gigachat import DEFAULT_BASE_URL, GigaChatProvider

        provider = GigaChatProvider(
            config.gigachat_auth_key,
            config.llm_model or "GigaChat",
            base_url=config.openai_base_url or DEFAULT_BASE_URL,
            scope=config.gigachat_scope,
            verify=config.gigachat_ca_bundle or True,
        )
    elif config.llm_provider == "openai_compatible":
        from ..llm.openai_compatible import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(
            config.openai_base_url,
            config.openai_api_key,
            config.llm_model,
            auth_scheme=config.llm_auth_scheme,
            max_tokens=config.llm_max_tokens,
        )
    else:
        provider = MockLLMProvider()

    return AnalysisPipeline(
        documents=documents,
        analyses=analyses,
        norm_store=norm_store,
        retriever=retriever,
        llm_provider=provider,
        audit=AuditTrail(config.audit_path),
        max_upload_bytes=config.max_upload_bytes,
    )

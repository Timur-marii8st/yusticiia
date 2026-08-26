from __future__ import annotations

import json
from pathlib import Path

from ..audit.trail import AuditTrail
from ..config import AppConfig, load_config
from ..domain.analysis import AnalysisReport
from ..domain.cases import ComparableCase
from ..domain.documents import Document
from ..legal_sources.store import NormStore
from ..llm.mock_provider import MockLLMProvider
from ..pipeline import AnalysisPipeline
from ..retrieval.case_retrieval import CaseRetriever
from ..storage.repositories import JsonFileRepository


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
    гибридный c pgvector (ADR-003, ADR-006)."""
    from ..config import load_config
    from ..legal_rag import (
        HashingTfidfEmbedder,
        LegalRag,
        OpenAICompatibleEmbeddingProvider,
    )

    config = load_config()
    if config.rag_mode not in ("hybrid", "hybrid_pgvector"):
        return LegalRag(norm_store)

    if config.embeddings_provider == "openai_compatible":
        embedder = OpenAICompatibleEmbeddingProvider(
            config.openai_base_url, config.openai_api_key, config.embedding_model
        )
    else:
        embedder = HashingTfidfEmbedder()

    if config.rag_mode == "hybrid_pgvector":
        if config.storage_backend != "postgres" or not config.database_url:
            # Конфигурация неполная — fallback к in-memory гибриду
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
        except Exception:  # noqa: BLE001
            return LegalRag(norm_store, embedder=embedder)

    return LegalRag(norm_store, embedder=embedder)


def build_pipeline(config: AppConfig | None = None) -> AnalysisPipeline:
    config = config or load_config()

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
    norm_store = NormStore.from_directory(config.fixtures_dir / "norms")
    retriever = CaseRetriever(load_cases(config.fixtures_dir))

    if config.llm_provider == "openai_compatible":
        from ..llm.openai_compatible import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(
            config.openai_base_url, config.openai_api_key, config.llm_model
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

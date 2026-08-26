from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("SO_TEST_DATABASE_URL"),
    reason="SO_TEST_DATABASE_URL не задан — пропускаем postgres-тесты",
)


@pytest.fixture()
def pg_dsn() -> str:
    return os.environ["SO_TEST_DATABASE_URL"]


def test_postgres_documents_roundtrip(pg_dsn: str) -> None:
    from second_opinion.domain.documents import Document
    from second_opinion.storage.postgres import PostgresJsonRepository

    table = f"documents_test_{uuid.uuid4().hex[:8]}"
    repo: PostgresJsonRepository = PostgresJsonRepository[Document](
        pg_dsn, table=table, model=Document, id_field="document_id"
    )

    doc = Document(
        document_id=uuid.uuid4().hex,
        filename="test.txt",
        content_type="text/plain",
        text="Документ для postgres-теста. Ч. 2 ст. 158 УК РФ.",
        sha256="abc",
    )
    repo.save(doc)
    loaded = repo.get(doc.document_id)
    assert loaded is not None
    assert loaded.text == doc.text
    assert loaded.sha256 == doc.sha256
    assert len(repo.list()) == 1
    assert repo.delete(doc.document_id) is True
    assert repo.get(doc.document_id) is None
    assert repo.delete(doc.document_id) is False


def test_postgres_analyses_roundtrip(pg_dsn: str) -> None:
    from second_opinion.domain.analysis import AnalysisReport
    from second_opinion.storage.postgres import PostgresJsonRepository

    table = f"analyses_test_{uuid.uuid4().hex[:8]}"
    repo: PostgresJsonRepository = PostgresJsonRepository[AnalysisReport](
        pg_dsn, table=table, model=AnalysisReport, id_field="analysis_id"
    )

    report = AnalysisReport(
        analysis_id=uuid.uuid4().hex,
        document_id="doc-1",
        applicable_at="2025-01-01",
        facts=[],
        evaluations=[],
        norms_applied=[],
        comparable_cases=[],
    )
    repo.save(report)
    loaded = repo.get(report.analysis_id)
    assert loaded is not None
    assert loaded.document_id == "doc-1"


def test_postgres_pipeline_e2e(pg_dsn: str, tmp_path) -> None:
    """Сквозной прогон конвейера на postgres-бэкенде."""
    from pathlib import Path

    from second_opinion.config import AppConfig

    # Уникальные таблицы на прогон, чтобы параллельные CI не мешали друг другу.
    suffix = uuid.uuid4().hex[:8]
    # Переопределяем таблицы через временное патчирование PostgresJsonRepository:
    # проще собрать pipeline вручную с кастомными таблицами.
    from second_opinion.audit.trail import AuditTrail
    from second_opinion.domain.analysis import AnalysisReport
    from second_opinion.domain.documents import Document
    from second_opinion.legal_sources.store import NormStore
    from second_opinion.llm.mock_provider import MockLLMProvider
    from second_opinion.pipeline import AnalysisPipeline
    from second_opinion.retrieval.case_retrieval import CaseRetriever
    from second_opinion.storage.postgres import PostgresJsonRepository

    fixtures = Path("data/fixtures")
    norm_store = NormStore.from_directory(fixtures / "norms")
    from second_opinion.api.deps import load_cases

    config = AppConfig(
        data_dir=tmp_path,
        fixtures_dir=fixtures,
        llm_provider="mock",
        openai_base_url="",
        openai_api_key="",
        llm_model="",
        max_upload_bytes=5_000_000,
        database_url=pg_dsn,
        storage_backend="postgres",
    )

    # Таблицы с суффиксом
    documents = PostgresJsonRepository[Document](
        pg_dsn, table=f"documents_{suffix}", model=Document, id_field="document_id"
    )
    analyses = PostgresJsonRepository[AnalysisReport](
        pg_dsn, table=f"analyses_{suffix}", model=AnalysisReport, id_field="analysis_id"
    )

    pipeline = AnalysisPipeline(
        documents=documents,  # type: ignore[arg-type]
        analyses=analyses,  # type: ignore[arg-type]
        norm_store=norm_store,
        retriever=CaseRetriever(load_cases(fixtures)),
        llm_provider=MockLLMProvider(),
        audit=AuditTrail(tmp_path / "audit.jsonl"),
        max_upload_bytes=config.max_upload_bytes,
    )

    text = (fixtures / "sample_documents" / "sample_158_special_clean.txt").read_bytes()
    document = pipeline.ingest("sample.txt", text)
    report = pipeline.analyze(document.document_id)
    assert report.facts
    assert report.evaluations
    # каскад удаления
    pipeline.delete_document(document.document_id)
    assert pipeline.get_analysis(report.analysis_id) is None

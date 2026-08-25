from __future__ import annotations

import uuid
from datetime import date

from .analytics.descriptive import compute_analytics
from .audit.trail import AuditTrail
from .domain.analysis import DEFAULT_DISCLAIMERS, AnalysisReport
from .domain.documents import Document
from .domain.norms import NormRef
from .fact_extraction.builder import build_case_facts
from .fact_extraction.llm_extractor import LLMFactExtractor
from .fact_extraction.pattern_extractor import PatternFactExtractor, _value_key
from .ingestion.parser import parse_document
from .legal_sources.store import NormStore
from .llm.provider import LLMProvider
from .retrieval.case_retrieval import CaseRetriever
from .rule_engine.engine import ENGINE_VERSION, RuleEngine
from .storage.repositories import JsonFileRepository


class DocumentNotFound(KeyError):
    pass


class AnalysisPipeline:
    """Сквозной конвейер: документ → факты → проверки → практика → отчёт."""

    def __init__(
        self,
        *,
        documents: JsonFileRepository[Document],
        analyses: JsonFileRepository[AnalysisReport],
        norm_store: NormStore,
        retriever: CaseRetriever,
        llm_provider: LLMProvider,
        audit: AuditTrail,
        max_upload_bytes: int,
        rule_engine: RuleEngine | None = None,
    ) -> None:
        self._documents = documents
        self._analyses = analyses
        self._norm_store = norm_store
        self._retriever = retriever
        self._llm = llm_provider
        self._audit = audit
        self._max_upload_bytes = max_upload_bytes
        self._engine = rule_engine or RuleEngine()
        self._pattern_extractor = PatternFactExtractor()
        self._llm_extractor = LLMFactExtractor(llm_provider, audit)

    @property
    def norm_store(self) -> NormStore:
        return self._norm_store

    @property
    def retriever(self) -> CaseRetriever:
        return self._retriever

    # -- загрузка документа -------------------------------------------------

    def ingest(self, filename: str, content: bytes) -> Document:
        document = parse_document(filename, content, self._max_upload_bytes)
        self._documents.save(document)
        self._audit.log(
            operation="document_ingest",
            component="ingestion",
            request_id=document.document_id,
            input_hash=document.sha256,
            details={"filename": document.filename, "chars": len(document.text)},
        )
        return document

    def get_document(self, document_id: str) -> Document:
        document = self._documents.get(document_id)
        if document is None:
            raise DocumentNotFound(f"документ не найден: {document_id}")
        return document

    # -- анализ -------------------------------------------------------------

    def analyze(
        self, document_id: str, applicable_at: str | None = None
    ) -> AnalysisReport:
        document = self.get_document(document_id)
        request_id = uuid.uuid4().hex

        pattern_facts = self._pattern_extractor.extract(document)
        known_keys = {(f.type.value, _value_key(f.value)) for f in pattern_facts}
        llm_facts = self._llm_extractor.extract(document, known_keys)
        facts = pattern_facts + llm_facts

        case_facts = build_case_facts(facts)
        resolved_date, assumed = self._resolve_applicable_at(applicable_at, case_facts.applicable_at)

        evaluations = self._engine.evaluate(
            case_facts,
            self._norm_store,
            resolved_date,
            audit=self._audit,
            request_id=request_id,
        )

        norms_applied: list[NormRef] = []
        seen: set[tuple[str, str]] = set()
        for evaluation in evaluations:
            for norm_ref in evaluation.norms_used:
                key = (norm_ref.norm_id, norm_ref.version_id)
                if key in seen:
                    continue
                seen.add(key)
                norms_applied.append(norm_ref)

        matches = self._retriever.search(case_facts)
        analytics = compute_analytics([match.case for match in matches])

        report = AnalysisReport(
            analysis_id=uuid.uuid4().hex,
            document_id=document_id,
            applicable_at=resolved_date.isoformat(),
            applicable_at_assumed=assumed,
            facts=facts,
            case_facts=case_facts,
            evaluations=evaluations,
            norms_applied=norms_applied,
            comparable_cases=matches,
            analytics=analytics,
            disclaimers=list(DEFAULT_DISCLAIMERS),
        )
        self._analyses.save(report)
        self._audit.log(
            operation="analysis_complete",
            component="pipeline",
            request_id=request_id,
            input_hash=document.sha256,
            output_hash=_hash_report(report),
            rule_version=ENGINE_VERSION,
            details={
                "document_id": document_id,
                "analysis_id": report.analysis_id,
                "facts": len(facts),
                "evaluations": _count_statuses(evaluations),
            },
        )
        return report

    def get_analysis(self, analysis_id: str) -> AnalysisReport | None:
        return self._analyses.get(analysis_id)

    def _resolve_applicable_at(
        self, explicit: str | None, extracted: str | None
    ) -> tuple[date, bool]:
        raw = explicit or extracted
        if raw:
            try:
                return date.fromisoformat(raw), False
            except ValueError:
                pass
        return date.today(), True


def _count_statuses(evaluations: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for evaluation in evaluations:
        counts[evaluation.status.value] = counts.get(evaluation.status.value, 0) + 1
    return counts


def _hash_report(report: AnalysisReport) -> str:
    import hashlib

    return hashlib.sha256(report.model_dump_json().encode("utf-8")).hexdigest()

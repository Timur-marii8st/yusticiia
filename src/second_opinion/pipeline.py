from __future__ import annotations

import uuid
from datetime import date

from .analytics.descriptive import compute_analytics
from .audit.trail import AuditTrail
from .domain.analysis import DEFAULT_DISCLAIMERS, AnalysisReport, AnalyticsSummary
from .domain.cases import CaseMatch
from .domain.documents import Document
from .domain.enums import ExtractionMethod, FactStatus
from .domain.facts import CaseFacts, LegalFact
from .domain.norms import NormRef
from .domain.rules import RuleEvaluation
from .fact_extraction.builder import build_case_facts
from .fact_extraction.llm_extractor import LLMFactExtractor
from .fact_extraction.pattern_extractor import PatternFactExtractor, _value_key
from .ingestion.parser import parse_document
from .legal_sources.store import NormStore
from .llm.provider import LLMProvider
from .logging_utils import StageTimer, log_stage
from .retrieval.case_retrieval import CaseRetriever
from .rule_engine.engine import ENGINE_VERSION, RuleEngine
from .storage.repositories import JsonFileRepository

UNSET_VALUE = object()


class DocumentNotFound(KeyError):
    pass


class AnalysisNotFound(KeyError):
    pass


class FactNotFound(KeyError):
    pass


#: Статусы, которые вправе устанавливать пользователь (человек в контуре).
USER_ALLOWED_STATUSES = (
    FactStatus.VERIFIED,
    FactStatus.UNCERTAIN,
    FactStatus.NOT_FOUND,
)


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

    @property
    def llm_provider(self) -> LLMProvider:
        return self._llm

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

        timer = StageTimer()
        pattern_facts = self._pattern_extractor.extract(document)
        known_keys = {(f.type.value, _value_key(f.value)) for f in pattern_facts}
        llm_facts = self._llm_extractor.extract(document, known_keys)
        facts = pattern_facts + llm_facts
        log_stage(
            request_id,
            "fact_extraction",
            duration_ms=timer.elapsed_ms(),
            document_id=document_id,
            pattern_facts=len(pattern_facts),
            llm_facts=len(llm_facts),
        )

        # Предварительный вывод — чтобы узнать извлечённую дату.
        preliminary = build_case_facts(_active_facts(facts))
        resolved_date, assumed = self._resolve_applicable_at(
            applicable_at, preliminary.applicable_at
        )
        log_stage(
            request_id,
            "applicable_date",
            document_id=document_id,
            applicable_at=resolved_date.isoformat(),
            assumed=assumed,
        )

        (case_facts, evaluations, norms_applied, matches, analytics) = self._derive(
            _active_facts(facts), resolved_date, request_id
        )

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
        log_stage(
            request_id,
            "analysis_complete",
            duration_ms=timer.elapsed_ms(),
            document_id=document_id,
            analysis_id=report.analysis_id,
            facts=len(facts),
            evaluations=_count_statuses(evaluations),
            comparable_cases=len(matches),
        )
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

    # -- человек в контуре: коррекция фактов ---------------------------------

    def update_fact(
        self,
        analysis_id: str,
        fact_id: str,
        *,
        status: str | None = None,
        value: object = UNSET_VALUE,
    ) -> AnalysisReport:
        """Скорректировать факт пользователем и пересчитать отчёт.

        Факт со статусом ``NOT_FOUND`` исключается из последующих проверок,
        но остаётся в отчёте для прозрачности истории правок.
        """
        report = self._analyses.get(analysis_id)
        if report is None:
            raise AnalysisNotFound(f"отчёт не найден: {analysis_id}")
        fact = next((f for f in report.facts if f.id == fact_id), None)
        if fact is None:
            raise FactNotFound(f"факт не найден: {fact_id}")

        old_status = fact.status
        status_changed = False
        if status is not None:
            try:
                new_status = FactStatus(status)
            except ValueError as exc:
                raise ValueError(f"неизвестный статус факта: {status}") from exc
            if new_status not in USER_ALLOWED_STATUSES:
                raise ValueError(
                    f"пользователь не может устанавливать статус {status}"
                )
            if new_status is FactStatus.VERIFIED and not fact.evidence:
                raise ValueError(
                    "нельзя подтвердить факт без доказательства (цитаты)"
                )
            fact.status = new_status
            if new_status is FactStatus.VERIFIED:
                fact.extraction_method = ExtractionMethod.USER
            status_changed = True

        value_changed = value is not UNSET_VALUE
        if value_changed:
            fact.value = value
            fact.extraction_method = ExtractionMethod.USER
            if not fact.evidence:
                raise ValueError(
                    "нельзя корректировать факт без привязки к фрагменту документа"
                )
            fact.status = FactStatus.VERIFIED
            status_changed = True

        request_id = uuid.uuid4().hex
        resolved_date = (
            date.fromisoformat(report.applicable_at)
            if report.applicable_at
            else date.today()
        )
        (case_facts, evaluations, norms_applied, matches, analytics) = self._derive(
            _active_facts(report.facts), resolved_date, request_id
        )
        report.case_facts = case_facts
        report.evaluations = evaluations
        report.norms_applied = norms_applied
        report.comparable_cases = matches
        report.analytics = analytics

        self._analyses.save(report)
        self._audit.log(
            operation="fact_update",
            component="pipeline",
            request_id=request_id,
            output_hash=_hash_report(report),
            details={
                "analysis_id": analysis_id,
                "fact_id": fact_id,
                "old_status": old_status.value,
                "new_status": fact.status.value,
                "status_changed": status_changed,
                "value_changed": value_changed,
            },
        )
        return report

    # -- общий вывод ----------------------------------------------------------

    def _derive(
        self, facts: list[LegalFact], applicable_at: date, request_id: str
    ) -> tuple[
        CaseFacts,
        list[RuleEvaluation],
        list[NormRef],
        list[CaseMatch],
        AnalyticsSummary,
    ]:
        case_facts = build_case_facts(facts)
        rules_timer = StageTimer()
        evaluations = self._engine.evaluate(
            case_facts,
            self._norm_store,
            applicable_at,
            audit=self._audit,
            request_id=request_id,
        )
        log_stage(
            request_id,
            "rule_engine",
            duration_ms=rules_timer.elapsed_ms(),
            engine_version=ENGINE_VERSION,
            statuses=_count_statuses(evaluations),
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
        retrieval_timer = StageTimer()
        matches = self._retriever.search(case_facts)
        log_stage(
            request_id,
            "case_retrieval",
            duration_ms=retrieval_timer.elapsed_ms(),
            corpus_size=self._retriever.total,
            matched=len(matches),
        )
        analytics = compute_analytics([match.case for match in matches])
        return case_facts, evaluations, norms_applied, matches, analytics

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


def _active_facts(facts: list[LegalFact]) -> list[LegalFact]:
    return [fact for fact in facts if fact.status is not FactStatus.NOT_FOUND]


def _count_statuses(evaluations: list[RuleEvaluation]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for evaluation in evaluations:
        counts[evaluation.status.value] = counts.get(evaluation.status.value, 0) + 1
    return counts


def _hash_report(report: AnalysisReport) -> str:
    import hashlib

    return hashlib.sha256(report.model_dump_json().encode("utf-8")).hexdigest()

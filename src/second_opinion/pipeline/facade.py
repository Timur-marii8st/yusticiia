"""Сквозной конвейер: документ -> факты -> проверки -> практика -> отчёт.

`AnalysisPipeline` — фасад: его публичный
интерфейс — это весь набор операций, которые умеет система (загрузить
документ, проанализировать, поправить факт, добавить факт, удалить,
посмотреть историю), а низкоуровневая механика вынесена в двух
сотрудников. `DocumentIngestion` отвечает за документ как таковой;
`AnalysisDerivation` — за пересчёт «факты -> проверки -> практика ->
аналитика», которым пользуются три разных сценария этого файла. Разделение
не меняет ничего снаружи: конструктор и методы фасада — те же, что были
до разделения на файлы.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date

from ..audit.trail import AuditTrail
from ..domain.analysis import DEFAULT_DISCLAIMERS, AnalysisReport
from ..domain.documents import Document
from ..domain.enums import ExtractionMethod, FactStatus, FactType
from ..domain.evidence import Evidence
from ..domain.facts import LegalFact
from ..fact_values import USER_ALLOWED_STATUSES, FactValidationError, validate_fact_value
from ..legal_sources.store import NormStore
from ..llm.provider import LLMProvider
from ..logging_utils import StageTimer, log_stage
from ..metrics import (
    ANALYSES_FAILED_TOTAL,
    ANALYSES_TOTAL,
    ANALYZE_DURATION_SECONDS,
    get_metrics,
)
from ..retrieval.case_retrieval import CaseRetriever
from ..rule_engine.engine import ENGINE_VERSION, RuleEngine
from ..storage.repositories import Repository
from .derivation import AnalysisDerivation, DerivedAnalysis, active_facts, count_statuses
from .errors import AnalysisNotFound, DocumentNotFound, FactNotFound
from .ingestion import DocumentIngestion

UNSET_VALUE = object()

__all__ = [
    "UNSET_VALUE",
    "AnalysisNotFound",
    "AnalysisPipeline",
    "DocumentNotFound",
    "FactNotFound",
]


class AnalysisPipeline:
    """Сквозной конвейер: документ -> факты -> проверки -> практика -> отчёт."""

    def __init__(
        self,
        *,
        documents: Repository[Document],
        analyses: Repository[AnalysisReport],
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
        self._ingestion = DocumentIngestion(
            documents=documents, audit=audit, max_upload_bytes=max_upload_bytes
        )
        self._derivation = AnalysisDerivation(
            norm_store=norm_store,
            retriever=retriever,
            llm_provider=llm_provider,
            audit=audit,
            rule_engine=rule_engine,
        )

    @property
    def max_upload_bytes(self) -> int:
        return self._ingestion.max_upload_bytes

    @property
    def norm_store(self) -> NormStore:
        return self._norm_store

    @property
    def retriever(self) -> CaseRetriever:
        return self._retriever

    @property
    def llm_provider(self) -> LLMProvider:
        return self._llm

    def ingest(
        self,
        filename: str,
        content: bytes,
        *,
        actor: str = "anonymous",
        request_id: str | None = None,
    ) -> Document:
        return self._ingestion.ingest(
            filename, content, actor=actor, request_id=request_id
        )

    def get_document(self, document_id: str) -> Document:
        return self._ingestion.get(document_id)

    def analyze(
        self,
        document_id: str,
        applicable_at: str | None = None,
        *,
        actor: str = "anonymous",
        request_id: str | None = None,
    ) -> AnalysisReport:
        request_id = request_id or uuid.uuid4().hex
        metrics = get_metrics()
        with metrics.time_histogram(ANALYZE_DURATION_SECONDS):
            try:
                document = self.get_document(document_id)
                facts, resolved_date, assumed = self._derivation.prepare(
                    document, applicable_at, request_id
                )
                derived = self._derivation.derive(
                    active_facts(facts), resolved_date, request_id
                )
                report = AnalysisReport(
                    analysis_id=uuid.uuid4().hex,
                    document_id=document_id,
                    owner=actor,
                    applicable_at=resolved_date.isoformat(),
                    applicable_at_assumed=assumed,
                    facts=facts,
                    disclaimers=list(DEFAULT_DISCLAIMERS),
                )
                self._persist_report(
                    report,
                    document,
                    derived,
                    timer=StageTimer(),
                    request_id=request_id,
                    actor=actor,
                )
                if metrics.enabled:
                    metrics.counter(ANALYSES_TOTAL).inc()
                return report
            except Exception:
                if metrics.enabled:
                    metrics.counter(ANALYSES_FAILED_TOTAL).inc()
                raise

    def get_analysis(self, analysis_id: str) -> AnalysisReport | None:
        return self._analyses.get(analysis_id)

    def history(
        self, *, owner: str | None = None, limit: int = 50
    ) -> list[tuple[AnalysisReport, Document | None]]:
        """История анализов, новые сверху.

        ``owner=None`` отдаёт все записи (для администратора); иначе —
        только принадлежащие указанному актору. Документ возвращается
        рядом с отчётом: без имени файла список неотличим от набора
        идентификаторов.

        Записи, сделанные до появления поля ``owner``, принадлежат пустой
        строке и в выдачу по владельцу не попадают. Это осознанно: лучше
        не показать своё старое дело, чем показать чужое.
        """
        reports = [
            report
            for report in self._analyses.list()
            if owner is None or report.owner == owner
        ]
        reports.sort(key=lambda r: r.created_at, reverse=True)
        return [
            (report, self._documents.get(report.document_id))
            for report in reports[:limit]
        ]

    def delete_document(self, document_id: str, *, actor: str = "anonymous") -> None:
        """Удалить документ и все отчёты по нему."""
        if not self._documents.delete(document_id):
            raise DocumentNotFound(f"документ не найден: {document_id}")
        for report in self._analyses.list():
            if report.document_id == document_id:
                self._analyses.delete(report.analysis_id)
        self._audit.log(
            operation="document_delete",
            actor=actor,
            component="pipeline",
            request_id=document_id,
            input_hash=document_id,
            details={"cascade_analyses_deleted": True},
        )

    def delete_analysis(self, analysis_id: str, *, actor: str = "anonymous") -> None:
        """Удалить один отчёт анализа."""
        if not self._analyses.delete(analysis_id):
            raise AnalysisNotFound(f"отчёт не найден: {analysis_id}")
        self._audit.log(
            operation="analysis_delete",
            actor=actor,
            component="pipeline",
            request_id=analysis_id,
            input_hash=analysis_id,
        )

    def update_fact(
        self,
        analysis_id: str,
        fact_id: str,
        *,
        status: str | None = None,
        value: object = UNSET_VALUE,
        reason: str | None = None,
        actor: str = "anonymous",
    ) -> AnalysisReport:
        """Скорректировать факт пользователем и пересчитать отчёт.

        Факт со статусом ``NOT_FOUND`` исключается из последующих проверок,
        но остаётся в отчёте для прозрачности истории правок. Исключение
        обязано нести причину (``reason``) — мера по риску № 5 Приложения
        № 3 к Концепции ВС РФ по ИИ («обязательная мотивировка отклонения
        ИИ-рекомендации»), а не только факт правки в аудит-журнале.
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
            if new_status is FactStatus.NOT_FOUND and not (reason and reason.strip()):
                raise ValueError(
                    "исключение факта требует причины (мотивировка отклонения "
                    "ИИ-рекомендации)"
                )
            fact.status = new_status
            fact.rejection_reason = (
                reason.strip() if new_status is FactStatus.NOT_FOUND and reason else None
            )
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
            fact.rejection_reason = None
            status_changed = True

        request_id = uuid.uuid4().hex
        resolved_date = self._date_from_report(report)
        derived = self._derivation.derive(
            active_facts(report.facts), resolved_date, request_id
        )
        self._derivation.apply(report, derived)
        self._analyses.save(report)
        self._audit.log(
            operation="fact_update",
            actor=actor,
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
                "reason": fact.rejection_reason,
            },
        )
        return report

    def add_fact(
        self,
        analysis_id: str,
        *,
        fact_type: str,
        value: object,
        quote: str,
        actor: str = "anonymous",
    ) -> AnalysisReport:
        """Судья добавляет обстоятельство вручную.

        Инвариант тот же, что для извлечённых фактов: цитата обязана
        дословно присутствовать в документе — иначе факт не создаётся.
        Факт сразу получает статус VERIFIED с методом ``user``.
        """
        report = self._analyses.get(analysis_id)
        if report is None:
            raise AnalysisNotFound(f"отчёт не найден: {analysis_id}")
        try:
            typed = FactType(fact_type)
        except ValueError as exc:
            raise FactValidationError(f"неизвестный тип факта: {fact_type}") from exc
        validate_fact_value(typed, value)

        document = self.get_document(report.document_id)
        quote = quote.strip()
        if not quote:
            raise FactValidationError("цитата пуста")
        start = document.text.find(quote)
        if start < 0:
            raise FactValidationError(
                "цитата не найдена в документе дословно; скопируйте её без изменений"
            )

        existing = {f.type.value for f in report.facts}
        counter = sum(1 for f in report.facts if f.type is typed) + 1
        fact_id = f"fact-{typed.value}-user-{counter}"
        while any(f.id == fact_id for f in report.facts):
            counter += 1
            fact_id = f"fact-{typed.value}-user-{counter}"

        report.facts.append(
            LegalFact(
                id=fact_id,
                type=typed,
                value=value,
                confidence=1.0,
                evidence=[
                    Evidence(
                        document_id=document.document_id,
                        quote=quote,
                        start_offset=start,
                        end_offset=start + len(quote),
                    )
                ],
                extraction_method=ExtractionMethod.USER,
                status=FactStatus.VERIFIED,
            )
        )

        request_id = uuid.uuid4().hex
        resolved_date = self._date_from_report(report)
        derived = self._derivation.derive(
            active_facts(report.facts), resolved_date, request_id
        )
        self._derivation.apply(report, derived)
        self._analyses.save(report)
        self._audit.log(
            operation="fact_add",
            actor=actor,
            component="pipeline",
            request_id=request_id,
            output_hash=_hash_report(report),
            details={
                "analysis_id": analysis_id,
                "fact_id": fact_id,
                "fact_type": typed.value,
                "types_before": sorted(existing),
            },
        )
        return report

    def _persist_report(
        self,
        report: AnalysisReport,
        document: Document,
        derived: DerivedAnalysis,
        *,
        timer: StageTimer,
        request_id: str,
        actor: str = "anonymous",
    ) -> None:
        """Применить `derived`, сохранить, залогировать новый анализ.

        Используется только из ``analyze`` (новые отчёты). Для пересчёта
        существующего отчёта (``update_fact``/``add_fact``) — отдельный
        путь: он не замеряет стадию ``analysis_complete``, потому что
        измерять там нечего, полного прохода не было.
        """
        self._derivation.apply(report, derived)
        self._analyses.save(report)
        log_stage(
            request_id,
            "analysis_complete",
            duration_ms=timer.elapsed_ms(),
            document_id=document.document_id,
            analysis_id=report.analysis_id,
            facts=len(report.facts),
            evaluations=count_statuses(report.evaluations),
            comparable_cases=len(report.comparable_cases),
        )
        self._audit.log(
            operation="analysis_complete",
            actor=actor,
            component="pipeline",
            request_id=request_id,
            input_hash=document.sha256,
            output_hash=_hash_report(report),
            rule_version=ENGINE_VERSION,
            details={
                "document_id": document.document_id,
                "analysis_id": report.analysis_id,
                "facts": len(report.facts),
                "evaluations": count_statuses(report.evaluations),
            },
        )

    def _date_from_report(self, report: AnalysisReport) -> date:
        """Восстановить дату из уже сохранённого отчёта (для пересчёта)."""
        if report.applicable_at:
            try:
                return date.fromisoformat(report.applicable_at)
            except ValueError:
                pass
        return date.today()


def _hash_report(report: AnalysisReport) -> str:
    return hashlib.sha256(report.model_dump_json().encode("utf-8")).hexdigest()

from __future__ import annotations

import uuid
from datetime import date

from .analytics.descriptive import compute_analytics
from .audit.trail import AuditTrail
from .domain.analysis import DEFAULT_DISCLAIMERS, AnalysisReport, AnalyticsSummary
from .domain.cases import CaseMatch
from .domain.documents import Document
from .domain.enums import ExtractionMethod, FactStatus, FactType, OffenseStage, PunishmentType
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
from .metrics import (
    ANALYSES_FAILED_TOTAL,
    ANALYSES_TOTAL,
    ANALYZE_DURATION_SECONDS,
    EXTRACT_DURATION_SECONDS,
    RETRIEVAL_DURATION_SECONDS,
    RULE_ENGINE_DURATION_SECONDS,
    RULE_EVALUATIONS,
    get_metrics,
)
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


class FactValidationError(ValueError):
    """Данные нового/исправляемого факта не проходят валидацию."""


_BOOLEAN_FACT_TYPES = frozenset(
    {
        FactType.PRIOR_CONVICTIONS,
        FactType.MINOR_DEPENDENTS,
        FactType.GUILTY_PLEA,
        FactType.SURRENDER_OR_CONFESSION,
        FactType.RESTITUTION,
        FactType.AGGRAVATING_RECIDIVISM,
        FactType.SPECIAL_PROCEDURE,
        FactType.JURY_TRIAL,
        FactType.SUSPENDED_SENTENCE,
    }
)

_GROUP_ROLES = frozenset(
    {"organized_group", "group_with_conspiracy", "group_of_persons"}
)


def _validate_fact_value(fact_type: FactType, value: object) -> None:
    """Схемная проверка значения нового факта (человек вводит руками)."""
    if fact_type in _BOOLEAN_FACT_TYPES:
        if not isinstance(value, bool):
            raise FactValidationError(
                f"значение факта {fact_type.value} должно быть true/false"
            )
        return
    if fact_type is FactType.DEFENDANT_AGE:
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 150:
            raise FactValidationError("defendant_age: ожидается целое число 0–150")
        return
    if fact_type is FactType.PUNISHMENT_TERM:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise FactValidationError("punishment_term: ожидается число месяцев ≥ 0")
        return
    if fact_type is FactType.QUALIFICATION:
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("article"), int)
            or isinstance(value.get("article"), bool)
            or value["article"] < 1
        ):
            raise FactValidationError(
                'qualification: ожидается {"article": int, "part": int|null}'
            )
        part = value.get("part")
        if part is not None and (isinstance(part, bool) or not isinstance(part, int) or part < 1):
            raise FactValidationError("qualification.part: целое число ≥ 1 или null")
        return
    if fact_type is FactType.DATE_OF_OFFENSE:
        try:
            date.fromisoformat(str(value))
        except ValueError as exc:
            raise FactValidationError(
                "date_of_offense: ожидается дата в формате ГГГГ-ММ-ДД"
            ) from exc
        return
    if fact_type is FactType.GROUP_OFFENSE:
        if value not in _GROUP_ROLES:
            raise FactValidationError(
                f"group_offense: одно из {sorted(_GROUP_ROLES)}"
            )
        return
    if fact_type is FactType.OFFENSE_STAGE:
        if value not in {stage.value for stage in OffenseStage}:
            raise FactValidationError(
                f"offense_stage: одно из {sorted(stage.value for stage in OffenseStage)}"
            )
        return
    if fact_type is FactType.PUNISHMENT_TYPE:
        if value not in {pt.value for pt in PunishmentType}:
            raise FactValidationError(
                f"punishment_type: одно из {sorted(pt.value for pt in PunishmentType)}"
            )
        return
    # HEALTH_FACTOR и остальные строковые типы: непустая строка.
    if not isinstance(value, str) or not value.strip():
        raise FactValidationError(f"{fact_type.value}: ожидается непустая строка")


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
        self.auth_service = None  # Опциональный сервис аутентификации

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
        request_id = uuid.uuid4().hex
        metrics = get_metrics()
        with metrics.time_histogram(ANALYZE_DURATION_SECONDS):
            try:
                document = self.get_document(document_id)
                facts, resolved_date, assumed = self._prepare(
                    document, applicable_at, request_id
                )
                derived = self._derive(_active_facts(facts), resolved_date, request_id)
                report = AnalysisReport(
                    analysis_id=uuid.uuid4().hex,
                    document_id=document_id,
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

    # -- удаление (право на забвение, docs/PRIVACY.md) ------------------------

    def delete_document(self, document_id: str) -> None:
        """Удалить документ и все отчёты по нему."""
        if not self._documents.delete(document_id):
            raise DocumentNotFound(f"документ не найден: {document_id}")
        for report in self._analyses.list():
            if report.document_id == document_id:
                self._analyses.delete(report.analysis_id)
        self._audit.log(
            operation="document_delete",
            component="pipeline",
            request_id=document_id,
            input_hash=document_id,
            details={"cascade_analyses_deleted": True},
        )

    def delete_analysis(self, analysis_id: str) -> None:
        """Удалить один отчёт анализа."""
        if not self._analyses.delete(analysis_id):
            raise AnalysisNotFound(f"отчёт не найден: {analysis_id}")
        self._audit.log(
            operation="analysis_delete",
            component="pipeline",
            request_id=analysis_id,
            input_hash=analysis_id,
        )

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
        resolved_date = self._date_from_report(report)
        derived = self._derive(_active_facts(report.facts), resolved_date, request_id)
        self._apply_derived(report, derived)
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

    # -- человек в контуре: добавление факта «с нуля» -------------------------

    def add_fact(
        self, analysis_id: str, *, fact_type: str, value: object, quote: str
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
        _validate_fact_value(typed, value)

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

        from .domain.evidence import Evidence

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
        derived = self._derive(_active_facts(report.facts), resolved_date, request_id)
        self._apply_derived(report, derived)
        self._analyses.save(report)
        self._audit.log(
            operation="fact_add",
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

    # -- общий вывод ----------------------------------------------------------

    def _prepare(
        self,
        document: Document,
        applicable_at: str | None,
        request_id: str,
    ) -> tuple[list[LegalFact], date, bool]:
        """Шаг 1 конвейера: извлечь факты и резолвить юридически значимую дату.

        Возвращает ``(facts, resolved_date, assumed)``. Не пишет в отчёт и
        не вызывает бизнес-логику, кроме экстракторов и валидации даты.
        """
        timer = StageTimer()
        metrics = get_metrics()
        with metrics.time_histogram(EXTRACT_DURATION_SECONDS):
            pattern_facts = self._pattern_extractor.extract(document)
            known_keys = {(f.type.value, _value_key(f.value)) for f in pattern_facts}
            llm_facts = self._llm_extractor.extract(document, known_keys)
        facts = pattern_facts + llm_facts
        if metrics.enabled:
            metrics.counter("second_opinion_facts_extracted_total").inc(len(facts))
        log_stage(
            request_id,
            "fact_extraction",
            duration_ms=timer.elapsed_ms(),
            document_id=document.document_id,
            pattern_facts=len(pattern_facts),
            llm_facts=len(llm_facts),
        )

        preliminary = build_case_facts(_active_facts(facts))
        resolved_date, assumed = self._resolve_applicable_at(
            applicable_at, preliminary.applicable_at
        )
        log_stage(
            request_id,
            "applicable_date",
            document_id=document.document_id,
            applicable_at=resolved_date.isoformat(),
            assumed=assumed,
        )
        return facts, resolved_date, assumed

    def _derive(
        self, facts: list[LegalFact], applicable_at: date, request_id: str
    ) -> tuple[
        CaseFacts,
        list[RuleEvaluation],
        list[NormRef],
        list[CaseMatch],
        AnalyticsSummary,
    ]:
        """Шаг 2: правила + поиск практики + аналитика + счётчики метрик."""
        case_facts = build_case_facts(facts)
        rules_timer = StageTimer()
        metrics = get_metrics()
        with metrics.time_histogram(RULE_ENGINE_DURATION_SECONDS):
            evaluations = self._engine.evaluate(
                case_facts,
                self._norm_store,
                applicable_at,
                audit=self._audit,
                request_id=request_id,
            )
        if metrics.enabled:
            metrics.counter(RULE_EVALUATIONS).inc(len(evaluations))
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
        with metrics.time_histogram(RETRIEVAL_DURATION_SECONDS):
            matches = self._retriever.search(case_facts)
        if metrics.enabled:
            metrics.counter("second_opinion_comparable_cases_total").inc(len(matches))
        log_stage(
            request_id,
            "case_retrieval",
            duration_ms=retrieval_timer.elapsed_ms(),
            corpus_size=self._retriever.total,
            matched=len(matches),
        )
        analytics = compute_analytics([match.case for match in matches])
        return case_facts, evaluations, norms_applied, matches, analytics

    def _apply_derived(
        self,
        report: AnalysisReport,
        derived: tuple[
            CaseFacts,
            list[RuleEvaluation],
            list[NormRef],
            list[CaseMatch],
            AnalyticsSummary,
        ],
    ) -> None:
        """Записать результат ``_derive`` в существующий отчёт (in-place)."""
        case_facts, evaluations, norms_applied, matches, analytics = derived
        report.case_facts = case_facts
        report.evaluations = evaluations
        report.norms_applied = norms_applied
        report.comparable_cases = matches
        report.analytics = analytics

    def _persist_report(
        self,
        report: AnalysisReport,
        document: Document,
        derived: tuple[
            CaseFacts,
            list[RuleEvaluation],
            list[NormRef],
            list[CaseMatch],
            AnalyticsSummary,
        ],
        *,
        timer: StageTimer,
        request_id: str,
    ) -> None:
        """Шаг 3 конвейера: применить derived, сохранить, залогировать.

        Используется только из ``analyze`` (новые отчёты). Для пересчёта
        существующего отчёта (``update_fact``/``add_fact``) — отдельный
        путь без таймера ``analyze_complete``.
        """
        self._apply_derived(report, derived)
        self._analyses.save(report)
        log_stage(
            request_id,
            "analysis_complete",
            duration_ms=timer.elapsed_ms(),
            document_id=document.document_id,
            analysis_id=report.analysis_id,
            facts=len(report.facts),
            evaluations=_count_statuses(report.evaluations),
            comparable_cases=len(report.comparable_cases),
        )
        self._audit.log(
            operation="analysis_complete",
            component="pipeline",
            request_id=request_id,
            input_hash=document.sha256,
            output_hash=_hash_report(report),
            rule_version=ENGINE_VERSION,
            details={
                "document_id": document.document_id,
                "analysis_id": report.analysis_id,
                "facts": len(report.facts),
                "evaluations": _count_statuses(report.evaluations),
            },
        )

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

    def _date_from_report(self, report: AnalysisReport) -> date:
        """Восстановить дату из уже сохранённого отчёта (для пересчёта)."""
        if report.applicable_at:
            try:
                return date.fromisoformat(report.applicable_at)
            except ValueError:
                pass
        return date.today()


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

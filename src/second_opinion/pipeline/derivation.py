"""Извлечение -> правила -> практика -> аналитика: общий вывод для отчёта.

Единственный коллаборатор, которым пользуются три разных сценария —
новый анализ (`analyze`), правка факта судьёй (`update_fact`) и ручное
добавление факта (`add_fact`). Каждый из них начинается с разного, но
заканчивается одним и тем же: список активных фактов пересчитывается в
проверки, практику и статистику, и результат применяется к отчёту. Вынос
в отдельный класс избавляет от повторения этого пересчёта в трёх местах
и делает сам пересчёт проверяемым независимо от того, что его вызвало.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..analytics.descriptive import compute_analytics
from ..audit.trail import AuditTrail
from ..domain.analysis import AnalysisReport, AnalyticsSummary, Concern, PracticePosition
from ..domain.cases import CaseMatch
from ..domain.documents import Document
from ..domain.enums import FactStatus, RuleStatus, VerificationStatus
from ..domain.facts import CaseFacts, LegalFact
from ..domain.norms import NormRef
from ..domain.rules import RuleEvaluation
from ..domain.source_fragments import NormSourceState
from ..fact_extraction.builder import build_case_facts
from ..fact_extraction.llm_extractor import LLMFactExtractor
from ..fact_extraction.pattern_extractor import PatternFactExtractor, _value_key
from ..legal_sources.acquisition import NormAcquisition
from ..legal_sources.coverage import HistoryCaveat, history_caveats
from ..legal_sources.store import NoApplicableVersionError, NormNotFoundError, NormStore
from ..llm.provider import LLMProvider
from ..logging_utils import StageTimer, log_stage
from ..metrics import (
    EXTRACT_DURATION_SECONDS,
    RETRIEVAL_DURATION_SECONDS,
    RULE_ENGINE_DURATION_SECONDS,
    RULE_EVALUATIONS,
    get_metrics,
)
from ..reporting import build_concerns, build_summary
from ..retrieval.case_retrieval import CaseRetriever
from ..rule_engine.engine import ENGINE_VERSION, RuleEngine


def active_facts(facts: list[LegalFact]) -> list[LegalFact]:
    """Факты, участвующие в проверках.

    Исключённые судьёй (``NOT_FOUND``) и факты модели, ждущие подтверждения
    (``LIKELY``), в проверки не идут: вывод правил не должен зависеть от
    непроверенного прочтения. Судья видит такой факт в отчёте и подтверждает его.
    """
    return [
        fact
        for fact in facts
        if fact.status not in (FactStatus.NOT_FOUND, FactStatus.LIKELY)
    ]


def count_statuses(evaluations: list[RuleEvaluation]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for evaluation in evaluations:
        counts[evaluation.status.value] = counts.get(evaluation.status.value, 0) + 1
    return counts


@dataclass(frozen=True)
class DerivedAnalysis:
    """Результат `AnalysisDerivation.derive` — то, что применяется к отчёту.

    Именованные поля вместо кортежа из пяти позиций: `apply` раскладывает
    их по отчёту по имени, а не по порядку, и добавление шестого поля не
    потребует править распаковку в трёх местах, где раньше жил кортеж.
    """

    case_facts: CaseFacts
    evaluations: list[RuleEvaluation]
    norms_applied: list[NormRef]
    comparable_cases: list[CaseMatch]
    analytics: AnalyticsSummary
    practice_positions: list[PracticePosition]
    norm_sources: list[NormSourceState]


class AnalysisDerivation:
    """Шаги 1–2 конвейера: извлечение фактов и вывод из них."""

    def __init__(
        self,
        *,
        norm_store: NormStore,
        retriever: CaseRetriever,
        llm_provider: LLMProvider,
        audit: AuditTrail,
        rule_engine: RuleEngine | None = None,
        norm_acquisition: NormAcquisition | None = None,
    ) -> None:
        self._norm_store = norm_store
        self._norm_acquisition = norm_acquisition
        self._retriever = retriever
        self._audit = audit
        self._engine = rule_engine or RuleEngine()
        self._pattern_extractor = PatternFactExtractor()
        self._llm_extractor = LLMFactExtractor(llm_provider, audit)

    def prepare(
        self,
        document: Document,
        applicable_at: str | None,
        request_id: str,
    ) -> tuple[list[LegalFact], date, bool]:
        """Извлечь факты и резолвить юридически значимую дату.

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

        preliminary = build_case_facts(active_facts(facts))
        resolved_date, assumed = self.resolve_applicable_at(
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

    def derive(
        self, facts: list[LegalFact], applicable_at: date, request_id: str
    ) -> DerivedAnalysis:
        """Правила + поиск практики + аналитика + счётчики метрик."""
        case_facts = build_case_facts(facts)
        source_requests: list[tuple[str, str, int | None]] = []
        required_ids: dict[tuple[str, str, int | None], str] = {}
        if case_facts.has_criminal_content and self._norm_acquisition:
            for qualification in case_facts.offense.qualifications:
                article = str(qualification.article)
                if qualification.article_suffix:
                    article += f".{qualification.article_suffix}"
                source_requests.append((qualification.code, article, qualification.part))
            for norm_id in self._engine.required_norm_ids:
                try:
                    norm = self._norm_store.get_norm_meta(norm_id)
                except NormNotFoundError:
                    # Общая часть УК имеет стабильные внутренние идентификаторы.
                    if norm_id.startswith("uk-rf:art-"):
                        article = norm_id.removeprefix("uk-rf:art-").replace("-", ".")
                        if all(piece.isdigit() for piece in article.split(".")):
                            request = ("УК РФ", article, None)
                            source_requests.append(request)
                            required_ids[request] = norm_id
                    continue
                if norm.code == "УК РФ":
                    article = str(norm.article) + (f".{norm.article_suffix}" if norm.article_suffix else "")
                    source_requests.append((norm.code, article, norm.part))
                    required_ids[(norm.code, article, norm.part)] = norm_id
        sources = self._norm_acquisition.resolve_many(source_requests, applicable_at) if self._norm_acquisition else []
        for source in sources:
            if not source.norm_id:
                source.norm_id = required_ids.get((source.code, source.article, source.part), "")
        rules_timer = StageTimer()
        metrics = get_metrics()
        with metrics.time_histogram(RULE_ENGINE_DURATION_SECONDS):
            evaluations = self._engine.evaluate(
                case_facts,
                self._norm_store,
                applicable_at,
                audit=self._audit,
                request_id=request_id,
                norm_sources=sources,
            )
        if metrics.enabled:
            metrics.counter(RULE_EVALUATIONS).inc(len(evaluations))
        log_stage(
            request_id,
            "rule_engine",
            duration_ms=rules_timer.elapsed_ms(),
            engine_version=ENGINE_VERSION,
            statuses=count_statuses(evaluations),
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
        analytics = compute_analytics(
            [match.case for match in matches],
            case_facts.sentence.term_months,
            punishment_type=case_facts.sentence.punishment_type,
        )
        return DerivedAnalysis(
            case_facts=case_facts,
            evaluations=evaluations,
            norms_applied=norms_applied,
            comparable_cases=matches,
            analytics=analytics,
            practice_positions=self.practice_positions(evaluations, date.today()),
            norm_sources=sources,
        )

    def practice_positions(
        self, evaluations: list[RuleEvaluation], on: date
    ) -> list[PracticePosition]:
        """Разъяснения и позиции практики ВС РФ к выполненным проверкам.

        Берутся нормы корпуса, у которых в ``applies_to`` названо правило; к
        неприменимым проверкам позиции не выводятся. Дата — день проверки
        проекта, а не дата деяния: разъяснения Пленума и Обзоры толкуют закон
        для дел, которые суд рассматривает сейчас, поэтому для деяния 2023 года
        применима и позиция Обзора 2024 года.
        """
        evaluated = [e.rule_id for e in evaluations if e.status is not RuleStatus.NOT_APPLICABLE]
        positions: list[PracticePosition] = []
        norms = sorted(self._norm_store.list_norms(), key=lambda n: (n.article, n.part or 0, n.part_suffix or ""))
        for rule_id in evaluated:
            for norm in norms:
                if rule_id not in norm.applies_to:
                    continue
                try:
                    version = self._norm_store.get_norm(norm.norm_id, on)
                except (NoApplicableVersionError, NormNotFoundError):
                    continue
                positions.append(
                    PracticePosition(
                        rule_id=rule_id,
                        norm_id=norm.norm_id,
                        version_id=version.version_id,
                        ref=norm.ref,
                        excerpt=_excerpt(version.text),
                        verification_status=version.verification_status.value,
                        source_url=version.source_url,
                    )
                )
        return positions

    def apply(self, report: AnalysisReport, derived: DerivedAnalysis) -> None:
        """Записать результат `derive` в существующий отчёт (in-place)."""
        report.case_facts = derived.case_facts
        report.evaluations = derived.evaluations
        report.norms_applied = derived.norms_applied
        report.norm_sources = derived.norm_sources
        report.comparable_cases = derived.comparable_cases
        report.analytics = derived.analytics
        report.practice_positions = derived.practice_positions
        report.summary = build_summary(derived.case_facts, report.facts)
        report.concerns = build_concerns(
            report,
            self.unverified_norms(derived.norms_applied),
            self.history_caveats(derived.norms_applied, report.applicable_at),
        )
        for source in report.norm_sources:
            if source.applicability != "confirmed" or source.freshness != "confirmed" or source.structured_data == "pending":
                report.concerns.append(Concern(
                    severity="data", title=f"Нормативный источник требует сверки: {source.ref}",
                    detail=" ".join(source.reasons),
                ))

    def history_caveats(
        self, norms_applied: list[NormRef], applicable_at: str | None
    ) -> list[HistoryCaveat]:
        """Оговорки о редакционной истории применённых норм на дату дела."""
        if not applicable_at:
            return []
        try:
            when = date.fromisoformat(applicable_at)
        except ValueError:
            return []
        caveats: list[HistoryCaveat] = []
        seen: set[str] = set()
        for norm_ref in norms_applied:
            if not norm_ref.version_id or norm_ref.norm_id in seen:
                continue
            seen.add(norm_ref.norm_id)
            try:
                norm = self._norm_store.get_norm_meta(norm_ref.norm_id)
                versions = self._norm_store.versions(norm_ref.norm_id)
            except (NormNotFoundError, KeyError):
                continue
            caveats += history_caveats(norm, versions, norm_ref.version_id, when)
        return caveats

    def unverified_norms(self, norms_applied: list[NormRef]) -> list[str]:
        """Ссылки на применённые нормы, чья редакция не прошла сверку.

        Тихо применённая несверенная редакция — та же беда, что тихо
        применённая свежая: вывод выглядит одинаково надёжным независимо
        от качества данных под ним.
        """
        unverified: list[str] = []
        for norm_ref in norms_applied:
            if not norm_ref.version_id:
                continue
            try:
                versions = self._norm_store.versions(norm_ref.norm_id)
            except (NormNotFoundError, KeyError):
                continue
            for version in versions:
                if (
                    version.version_id == norm_ref.version_id
                    and version.verification_status is not VerificationStatus.VERIFIED
                ):
                    unverified.append(norm_ref.ref)
        return unverified

    @staticmethod
    def resolve_applicable_at(
        explicit: str | None, extracted: str | None
    ) -> tuple[date, bool]:
        raw = explicit or extracted
        if raw:
            try:
                return date.fromisoformat(raw), False
            except ValueError:
                pass
        return date.today(), True


EXCERPT_CHARS = 360


def _excerpt(text: str) -> str:
    """Начало позиции до границы предложения или слова, не длиннее EXCERPT_CHARS."""
    if len(text) <= EXCERPT_CHARS:
        return text
    cut = text[:EXCERPT_CHARS]
    sentence = cut.rfind(". ")
    if sentence > EXCERPT_CHARS // 2:
        return cut[: sentence + 1]
    return cut.rsplit(" ", 1)[0] + "…"

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from .cases import CaseMatch
from .facts import CaseFacts, LegalFact
from .norms import NormRef
from .rules import RuleEvaluation


class SentencePosition(BaseModel):
    """Где назначенный срок стоит в распределении по сопоставимым делам.

    Это **описательное наблюдение, а не оценка справедливости**. Выборка
    синтетическая или малая, отбор дел неслучаен, а назначение наказания
    зависит от обстоятельств, которых в сравнении нет. Поэтому здесь нет
    ни «нормы», ни «отклонения от нормы» — только положение числа в ряду
    чисел, и `band` названа так, чтобы это было видно.

    ``band``: ``below`` — ниже нижней четверти, ``typical`` — между P25 и
    P75, ``above`` — выше верхней четверти, ``outside`` — за пределами
    всего наблюдаемого диапазона.
    """

    term_months: float
    n_compared: int
    band: str
    share_at_or_below: float
    note: str = ""


class AnalyticsSummary(BaseModel):
    """Описательная (НЕ предписывающая) статистика по выборке дел."""

    n_cases: int
    n_with_term: int = 0
    median_months: float | None = None
    mean_months: float | None = None
    p25_months: float | None = None
    p75_months: float | None = None
    min_months: float | None = None
    max_months: float | None = None
    punishment_type_distribution: dict[str, int] = Field(default_factory=dict)
    suspended_share: float | None = None
    feature_spread: dict[str, float] = Field(default_factory=dict)
    sentence_position: SentencePosition | None = None


class SummaryLine(BaseModel):
    """Строка изложения дела вместе с обстоятельствами, из которых собрана.

    Изложение не пересказывается моделью и не пишется свободным текстом:
    оно собирается из извлечённых обстоятельств, и каждая строка знает,
    на каких из них держится. Иначе краткое изложение стало бы
    единственным местом отчёта, которое нельзя раскрыть до цитаты.
    """

    text: str
    fact_ids: list[str] = Field(default_factory=list)


class CaseSummary(BaseModel):
    """Краткое изложение дела: заголовок и строки по существу."""

    headline: str = ""
    lines: list[SummaryLine] = Field(default_factory=list)


class Concern(BaseModel):
    """Проблемное место: то, на что судье стоит посмотреть отдельно.

    ``severity``: ``violation`` — проверка не сошлась; ``attention`` —
    предупреждение движка; ``data`` — вывод ограничен данными, а не
    правом (нехватка обстоятельств, несверенная редакция нормы,
    подставленная дата).
    """

    severity: str
    title: str
    detail: str = ""
    rule_id: str = ""
    fact_ids: list[str] = Field(default_factory=list)


class PracticePosition(BaseModel):
    """Разъяснение Пленума или позиция практики ВС РФ, относящаяся к проверке.

    Связь задана в корпусе полем ``applies_to`` нормы, а не подобрана поиском:
    позиция попадает в заключение потому, что юрист (или черновик импорта)
    отнёс её к этому правилу. Статус сверки показывается рядом.
    """

    rule_id: str
    norm_id: str
    version_id: str
    ref: str
    excerpt: str
    verification_status: str
    source_url: str = ""


class AnalysisReport(BaseModel):
    """Итог анализа: второе мнение с полной прослеживаемостью."""

    analysis_id: str
    document_id: str
    owner: str = ""
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(microsecond=0)
    )
    applicable_at: str | None = None
    applicable_at_assumed: bool = False
    facts: list[LegalFact] = Field(default_factory=list)
    case_facts: CaseFacts = Field(default_factory=CaseFacts)
    summary: CaseSummary = Field(default_factory=CaseSummary)
    concerns: list[Concern] = Field(default_factory=list)
    evaluations: list[RuleEvaluation] = Field(default_factory=list)
    norms_applied: list[NormRef] = Field(default_factory=list)
    comparable_cases: list[CaseMatch] = Field(default_factory=list)
    practice_positions: list[PracticePosition] = Field(default_factory=list)
    analytics: AnalyticsSummary | None = None
    disclaimers: list[str] = Field(default_factory=list)


DEFAULT_DISCLAIMERS = [
    "Система является вспомогательным инструментом проверки и не заменяет судью.",
    "Ни один вывод системы не является обязательным для суда.",
    "Статистика по сопоставимым делам носит исключительно описательный характер "
    "и не является основанием для назначения наказания.",
    "Извлечённые обстоятельства требуют проверки судьёй по исходному тексту документа.",
]

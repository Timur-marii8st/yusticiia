from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, model_validator


class ComparableCase(BaseModel):
    """Судебный акт в базе практики (для сопоставления).

    Фикстуры обязаны иметь ``synthetic=True`` и не выдаваться за реальную
    практику (см. docs/LEGAL_SAFETY_PRINCIPLES.md). Реальное дело
    (``synthetic=False``) обязано нести ``source_url``: утверждение
    «это реальная практика» проверяемо ровно настолько, насколько его
    можно раскрыть до источника — тот же инвариант, что для норм и
    цитат, применённый к практике.
    """

    case_id: str
    title: str
    court: str
    date: date
    article: int
    part: int | None = None
    stage: str = "completed"  # OffenseStage.value
    recidivism: bool = False
    special_procedure: bool = False
    guilty_plea: bool = False
    jury_trial: bool = False
    group: bool = False
    mitigating_codes: list[str] = Field(default_factory=list)
    aggravating_codes: list[str] = Field(default_factory=list)
    punishment_type: str = "imprisonment"  # PunishmentType.value
    term_months: float | None = None
    suspended: bool = False
    summary: str = ""
    synthetic: bool = True
    #: Ссылка на источник — обязательна для реальных дел (см. валидатор
    #: ниже). У синтетических фикстур остаётся пустой: источника нет,
    #: и подделывать его пустой строкой смысла нет.
    source_url: str = ""

    @model_validator(mode="after")
    def _real_case_names_its_source(self) -> ComparableCase:
        if not self.synthetic and not self.source_url:
            raise ValueError(
                f"дело {self.case_id}: synthetic=False без source_url — "
                "реальная практика обязана быть прослеживаема до источника"
            )
        return self


class CaseMatch(BaseModel):
    """Найденное сопоставимое дело с объяснением отбора."""

    case: ComparableCase
    reasons: list[str]
    score: float

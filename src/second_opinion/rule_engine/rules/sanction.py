"""Пределы санкции статьи (R-001)."""

from __future__ import annotations

from dataclasses import dataclass

from ...domain.enums import FactType, PunishmentType, RuleStatus
from ...domain.facts import CaseFacts
from ...domain.norms import NormRef, NormVersion, SanctionSpec, sanction_for
from ...domain.rules import RuleEvaluation
from ...legal_sources.store import NoApplicableVersionError, NormNotFoundError
from .base import (
    _BaseSanctionRule,
    _facts_of_type,
)


@dataclass(frozen=True)
class _Measure:
    """Как измеряется вид наказания, не связанный со сроком в месяцах."""

    fact_type: FactType
    missing: str
    unit: str
    low: str
    high: str


_MEASURES = {
    PunishmentType.COMPULSORY_LABOR.value: _Measure(
        FactType.PUNISHMENT_HOURS, "срок обязательных работ в часах", "ч.", "min_hours", "max_hours"
    ),
    PunishmentType.FINE.value: _Measure(
        FactType.FINE_AMOUNT, "размер штрафа в рублях", "руб.", "min_rubles", "max_rubles"
    ),
}


def _amount(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


class SanctionRangeRule(_BaseSanctionRule):
    """Назначенный срок должен лежать в пределах санкции статьи (ст. 60 УК РФ)."""

    rule_id = "R-001"
    version = "1.0.0"
    title = "Пределы санкции статьи"
    description = (
        "Проверяет, что назначенный срок лишения свободы не превышает максимум "
        "санкции статьи квалификации и не ниже минимума без специальных оснований."
    )
    norm_refs = ["uk-rf:art-60"]

    def evaluate(self, context) -> RuleEvaluation:
        measure = _MEASURES.get(context.case_facts.sentence.punishment_type or "")
        if measure is not None:
            return self._evaluate_measured(context, measure)
        early, data = self._prepare(context)
        if early is not None:
            return early
        case: CaseFacts = data["case"]
        version: NormVersion = data["version"]
        max_months: float = data["max_months"]
        term = float(case.sentence.term_months or 0)
        norms_used = self._norm_refs(version, data["norm"].ref) + [
            NormRef(norm_id="uk-rf:art-60", version_id="", ref="УК РФ ст. 60")
        ]
        facts_used = [
            f.id
            for f in _facts_of_type(case, FactType.PUNISHMENT_TERM)
            + _facts_of_type(case, FactType.PUNISHMENT_TYPE)
            + _facts_of_type(case, FactType.QUALIFICATION)
        ]
        numbers = {"term_months": term, "max_months": max_months}
        if term > max_months:
            return self._evaluation(
                status=RuleStatus.FAIL,
                headline=f"{self.title}: возможно превышение максимума санкции",
                explanation=(
                    f"Назначено {term:.0f} мес. лишения свободы, максимум санкции "
                    f"{data['qualification'].ref} — {max_months:.0f} мес. "
                    "Проверьте квалификацию и основания выхода за предел санкции."
                ),
                facts_used=facts_used,
                norms_used=norms_used,
                numbers=numbers,
            )
        sanction_min = data["sanction"].min_months
        if sanction_min and term < sanction_min:
            cited = bool(_facts_of_type(case, FactType.EXCEPTIONAL_CIRCUMSTANCES))
            explanation = (
                f"Назначено {term:.0f} мес. при минимуме санкции "
                f"{sanction_min:.0f} мес. Назначение ниже низшего предела "
                "возможно только по основаниям ст. 64 УК РФ — "
            )
            explanation += (
                "статья упомянута в тексте документа, проверьте обоснованность "
                "применения."
                if cited
                else "статья в тексте документа не упомянута ни разу, проверьте, "
                "не пропущено ли основание."
            )
            return self._evaluation(
                status=RuleStatus.WARNING,
                headline=f"{self.title}: срок ниже минимума санкции",
                explanation=explanation,
                facts_used=facts_used
                + [f.id for f in _facts_of_type(case, FactType.EXCEPTIONAL_CIRCUMSTANCES)],
                norms_used=norms_used + [
                    NormRef(norm_id="uk-rf:art-64", version_id="", ref="УК РФ ст. 64")
                ],
                numbers=numbers | {"min_months": float(sanction_min)},
            )
        return self._evaluation(
            status=RuleStatus.PASS,
            headline=f"{self.title}: в пределах санкции",
            explanation=(
                f"Назначенный срок {term:.0f} мес. находится в пределах санкции "
                f"{data['qualification'].ref} (максимум {max_months:.0f} мес.)."
            ),
            facts_used=facts_used,
            norms_used=norms_used,
            numbers=numbers,
        )

    def _evaluate_measured(self, context, measure: _Measure) -> RuleEvaluation:
        """Штраф (рубли) и обязательные работы (часы) против пределов санкции."""
        case: CaseFacts = context.case_facts
        kind = case.sentence.punishment_type or ""
        value = case.sentence.fine_rubles if measure.unit == "руб." else case.sentence.hours
        qualification = case.primary_qualification
        missing = [m for m, absent in ((measure.missing, value is None),
                   ("квалификация (статья УК РФ)", qualification is None)) if absent]
        if missing or value is None or qualification is None:
            return self._unknown(
                missing,
                "Для проверки не хватает извлечённых данных. Система не делает "
                "предположений: проверьте документ вручную.",
            )
        norm = context.norm_store.find_by_article(
            qualification.code, qualification.article, qualification.part,
            qualification.article_suffix,
        )
        if norm is None:
            return self._unknown(
                [f"норма для {qualification.ref} в базе источников не найдена"],
                "Санкция статьи отсутствует в базе источников. Система не "
                "выдумывает норму: добавьте источник или проверьте вручную.",
            )
        try:
            version = context.norm_store.get_norm(norm.norm_id, context.applicable_at)
        except (NoApplicableVersionError, NormNotFoundError):
            return self._unknown(
                [f"редакция {norm.ref} на дату {context.applicable_at.isoformat()}"],
                "Не найдена редакция нормы, действовавшая на юридически значимую "
                "дату. Система не применяет редакцию молча.",
            )
        sanction: SanctionSpec | None = sanction_for(
            version.sanctions, kind, context.applicable_at
        )
        high = getattr(sanction, measure.high) if sanction is not None else None
        if sanction is None or high is None:
            return self._unknown(
                [f"предел ({measure.unit}) по {norm.ref} на {context.applicable_at.isoformat()}"],
                "В корпусе для этой редакции предел данного вида наказания не внесён. "
                "Проверьте санкцию по тексту нормы вручную.",
            )
        low = getattr(sanction, measure.low)
        facts_used = [
            f.id
            for f in _facts_of_type(case, measure.fact_type)
            + _facts_of_type(case, FactType.PUNISHMENT_TYPE)
            + _facts_of_type(case, FactType.QUALIFICATION)
        ]
        norms_used = self._norm_refs(version, norm.ref) + [
            NormRef(norm_id="uk-rf:art-60", version_id="", ref="УК РФ ст. 60")
        ]
        numbers = {"amount": float(value), "max": float(high)}
        pending = (
            " Предел внесён по тексту ИПС и ещё не сверен юристом."
            if sanction.pending_review
            else ""
        )
        if value > high:
            return self._evaluation(
                status=RuleStatus.FAIL,
                headline=f"{self.title}: возможно превышение максимума санкции",
                explanation=(
                    f"Назначено {_amount(value)} {measure.unit}, максимум санкции "
                    f"{qualification.ref} — {_amount(high)} {measure.unit} Проверьте "
                    "квалификацию и основания выхода за предел санкции." + pending
                ),
                facts_used=facts_used,
                norms_used=norms_used,
                numbers=numbers,
            )
        if low and value < low:
            cited = bool(_facts_of_type(case, FactType.EXCEPTIONAL_CIRCUMSTANCES))
            return self._evaluation(
                status=RuleStatus.WARNING,
                headline=f"{self.title}: ниже минимума санкции",
                explanation=(
                    f"Назначено {_amount(value)} {measure.unit} при минимуме санкции "
                    f"{_amount(low)} {measure.unit} Ниже низшего предела можно только по "
                    "ст. 64 УК РФ — "
                    + (
                        "статья упомянута в документе, проверьте обоснованность."
                        if cited
                        else "статья в документе не упомянута, проверьте основание."
                    )
                    + pending
                ),
                facts_used=facts_used,
                norms_used=norms_used
                + [NormRef(norm_id="uk-rf:art-64", version_id="", ref="УК РФ ст. 64")],
                numbers=numbers | {"min": float(low)},
            )
        return self._evaluation(
            status=RuleStatus.PASS,
            headline=f"{self.title}: в пределах санкции",
            explanation=(
                f"Назначено {_amount(value)} {measure.unit}, санкция {qualification.ref} "
                f"допускает до {_amount(high)} {measure.unit}" + pending
            ),
            facts_used=facts_used,
            norms_used=norms_used,
            numbers=numbers,
        )

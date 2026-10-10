"""Черновые правила: рецидив (ст. 68), несовершеннолетние (ст. 88), обратная сила (ст. 10).

Не входят в ``DEFAULT_RULES``: нормы-основания в корпусе — черновики из ИПС,
юрист их не сверял (инвариант И-10). Подключение — строкой в реестре после
сверки. Все три правила выдают только WARNING, UNKNOWN, PASS или
NOT_APPLICABLE: вывод о нарушении делает судья.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ...domain.enums import FactType, PunishmentType, RuleStatus
from ...domain.facts import CaseFacts
from ...domain.norms import LegalNorm, NormRef, NormVersion, SanctionSpec, sanction_for
from ...domain.rules import LegalRule, RuleEvaluation
from ...legal_sources.store import NoApplicableVersionError, NormNotFoundError
from ..limitation import crime_category
from .base import IMPRISONMENT, _facts_of_type, _versioned_ref

STAGE_FRACTIONS = {"attempt": 3 / 4, "preparation": 1 / 2}
MINOR_AGE = 18
YOUNG_MINOR_AGE = 16
MINOR_YOUNG_MAX_MONTHS = 6 * 12
MINOR_MAX_MONTHS = 10 * 12
MINOR_HOURS = (40.0, 160.0)
MINOR_FINE = (1_000.0, 50_000.0)
MINOR_CORRECTIONAL_MAX_MONTHS = 12.0
MINOR_TYPES = {
    PunishmentType.FINE.value,
    PunishmentType.COMPULSORY_LABOR.value,
    PunishmentType.CORRECTIONAL_LABOR.value,
    PunishmentType.RESTRICTION_OF_LIBERTY.value,
    PunishmentType.IMPRISONMENT.value,
}


@dataclass(frozen=True)
class _Sanction:
    norm: LegalNorm
    version: NormVersion
    imprisonment: SanctionSpec | None


def _sanction(context) -> _Sanction | list[str]:
    """Норма квалификации в редакции на дату и запись лишения свободы, либо что не найдено."""
    case: CaseFacts = context.case_facts
    qualification = case.primary_qualification
    if qualification is None:
        return ["квалификация (статья УК РФ)"]
    norm = context.norm_store.find_by_article(
        qualification.code, qualification.article, qualification.part, qualification.article_suffix
    )
    if norm is None:
        return [f"норма для {qualification.ref} в базе источников не найдена"]
    try:
        version = context.norm_store.get_norm(norm.norm_id, context.applicable_at)
    except (NoApplicableVersionError, NormNotFoundError):
        return [f"редакция {norm.ref} на дату {context.applicable_at.isoformat()}"]
    return _Sanction(norm, version, sanction_for(version.sanctions, IMPRISONMENT, context.applicable_at))


def _refs(context, version: NormVersion, norm: LegalNorm, base_id: str, base_ref: str) -> list[NormRef]:
    return [
        NormRef(norm_id=version.norm_id, version_id=version.version_id, ref=norm.ref),
        _versioned_ref(context, base_id, base_ref),
    ]


class RecidivismMinimumRule(LegalRule):
    """При рецидиве срок не ниже трети максимума наиболее строгого вида (ч. 2 ст. 68)."""

    rule_id = "R-D04"
    version = "0.1.0-draft"
    title = "Срок при рецидиве (ч. 2, 3 ст. 68 УК РФ)"
    description = (
        "При любом виде рецидива назначается наиболее строгий вид наказания санкции, и срок не "
        "может быть меньше трети его максимума; меньше — только при смягчающих по ст. 61 "
        "(ч. 3 ст. 68) или по ст. 64 УК РФ (пп. 47–49 ППВС № 58, Обзор ВС РФ от 18.12.2024, п. 19)."
    )
    norm_refs = ["uk-rf:art-68"]

    def evaluate(self, context) -> RuleEvaluation:
        case: CaseFacts = context.case_facts
        recidivism = [f for f in case.aggravating if f.code == "63.1.а"]
        if not recidivism:
            return self._evaluation(
                status=RuleStatus.NOT_APPLICABLE,
                headline=f"{self.title}: не применимо",
                explanation="Рецидив отягчающим обстоятельством в проекте не признан.",
            )
        found = _sanction(context)
        if isinstance(found, list):
            return self._unknown(found, "Без санкции статьи на дату деяния предел не вычислить.")
        if found.imprisonment is None or found.imprisonment.max_months is None:
            return self._unknown(
                [f"предел лишения свободы по {found.norm.ref}"],
                "Наиболее строгий вид наказания в корпусе не описан числом.",
            )
        facts_used = [fid for f in recidivism for fid in f.fact_ids] + [
            f.id for f in _facts_of_type(case, FactType.PUNISHMENT_TYPE) + _facts_of_type(case, FactType.PUNISHMENT_TERM)
        ]
        norms_used = _refs(context, found.version, found.norm, "uk-rf:art-68", "УК РФ ст. 68")
        kind = case.sentence.punishment_type
        mitigating = bool(case.mitigating)
        exceptional = bool(_facts_of_type(case, FactType.EXCEPTIONAL_CIRCUMSTANCES))
        if kind is None:
            return self._unknown(["вид назначенного наказания"], "Вид наказания не извлечён.")
        if kind != IMPRISONMENT:
            basis = (
                "в проекте есть смягчающие по ст. 61 — менее строгий вид возможен по ч. 3 ст. 68, "
                "проверьте мотивировку"
                if mitigating or exceptional
                else "смягчающих по ст. 61 и ссылки на ст. 64 в проекте нет"
            )
            return self._evaluation(
                status=RuleStatus.WARNING,
                headline=f"{self.title}: назначен не наиболее строгий вид наказания",
                explanation=(
                    "При рецидиве назначается наиболее строгий вид наказания санкции — лишение "
                    f"свободы (ч. 2 ст. 68, п. 47 ППВС № 58); назначено иное, {basis}."
                ),
                facts_used=facts_used,
                norms_used=norms_used,
            )
        term = case.sentence.term_months
        if term is None:
            return self._unknown(["срок лишения свободы"], "Срок не извлечён.")
        maximum = float(found.imprisonment.max_months)
        stage = case.offense.stage or "completed"
        if stage in STAGE_FRACTIONS:
            if not case.procedural.special_procedure:
                return self._unknown(
                    ["правило расчёта трети для неоконченного преступления в общем порядке"],
                    "Для неоконченного преступления треть исчисляется от предела по ст. 66 (п. 49 ППВС № 58 — "
                    "для особого порядка); для общего порядка расчёт не формализован, проверьте вручную.",
                )
            maximum *= STAGE_FRACTIONS[stage]
        limit = maximum / 3
        numbers = {"term_months": float(term), "max_months": maximum, "limit_months": round(limit, 2)}
        if term >= limit:
            return self._evaluation(
                status=RuleStatus.PASS,
                headline=f"{self.title}: не ниже трети максимума",
                explanation=f"Назначено {term:g} мес., треть максимума {maximum:g} мес. — {limit:.1f} мес.",
                facts_used=facts_used,
                norms_used=norms_used,
                numbers=numbers,
            )
        if mitigating or exceptional:
            basis = "ст. 64 УК РФ упомянута" if exceptional else "в проекте есть смягчающие по ст. 61 УК РФ"
            explanation = (
                f"Назначено {term:g} мес. — меньше трети максимума ({limit:.1f} мес.); {basis}, поэтому "
                "срок ниже трети возможен по ч. 3 ст. 68 или ст. 64. Проверьте, что суд обсудил и "
                "мотивировал это (п. 35 ППВС № 58, Обзор ВС РФ от 18.12.2024, п. 19)."
            )
        else:
            explanation = (
                f"Назначено {term:g} мес. — меньше трети максимума ({limit:.1f} мес.), а смягчающих по "
                "ст. 61 и ссылки на ст. 64 в проекте нет: возможно нарушение ч. 2 ст. 68 УК РФ."
            )
        return self._evaluation(
            status=RuleStatus.WARNING,
            headline=f"{self.title}: срок ниже трети максимума",
            explanation=explanation,
            facts_used=facts_used,
            norms_used=norms_used,
            numbers=numbers,
        )


class MinorSentenceRule(LegalRule):
    """Виды и пределы наказаний несовершеннолетним (ст. 88 УК РФ)."""

    rule_id = "R-D05"
    version = "0.1.0-draft"
    title = "Наказание несовершеннолетнему (ст. 88 УК РФ)"
    description = (
        "Виды наказаний несовершеннолетним (ч. 1), штраф 1–50 тыс. руб. (ч. 2), обязательные работы "
        "40–160 ч (ч. 3), исправительные работы до года (ч. 4), лишение свободы не свыше 6 или 10 "
        "лет и запрет для впервые осуждённых за небольшую и среднюю тяжесть (ч. 6), низший предел "
        "вдвое для тяжких и особо тяжких (ч. 6.1)."
    )
    norm_refs = ["uk-rf:art-88"]

    def evaluate(self, context) -> RuleEvaluation:
        case: CaseFacts = context.case_facts
        age = case.defendant.age
        if age is None:
            return self._unknown(["возраст подсудимого"], "Ст. 88 применяется по возрасту на момент деяния.")
        if age >= MINOR_AGE + 2:
            return self._evaluation(
                status=RuleStatus.NOT_APPLICABLE,
                headline=f"{self.title}: не применимо",
                explanation=f"Подсудимому {age} лет.",
            )
        if age >= MINOR_AGE:
            return self._unknown(
                ["возраст на момент деяния"],
                f"Подсудимому {age} лет: на момент деяния он мог быть несовершеннолетним. Проверьте дату рождения.",
            )
        found = _sanction(context)
        if isinstance(found, list):
            return self._unknown(found, "Без санкции статьи категорию преступления не определить.")
        norms_used = _refs(context, found.version, found.norm, "uk-rf:art-88", "УК РФ ст. 88")
        facts_used = [f.id for f in _facts_of_type(case, FactType.DEFENDANT_AGE) + _facts_of_type(case, FactType.PUNISHMENT_TYPE)]
        findings: list[str] = []
        unknown: list[str] = []
        kind = case.sentence.punishment_type
        if kind is not None and kind not in MINOR_TYPES:
            findings.append("вид наказания не входит в перечень ч. 1 ст. 88 УК РФ")
        hours = case.sentence.hours
        if kind == PunishmentType.COMPULSORY_LABOR.value and hours is not None and not MINOR_HOURS[0] <= hours <= MINOR_HOURS[1]:
            findings.append(f"обязательные работы {hours:g} ч вне пределов 40–160 ч (ч. 3)")
        fine = case.sentence.fine_rubles
        if kind == PunishmentType.FINE.value and fine is not None and not MINOR_FINE[0] <= fine <= MINOR_FINE[1]:
            findings.append(f"штраф {fine:,.0f} руб. вне пределов 1 000–50 000 руб. (ч. 2)".replace(",", " "))
        term = case.sentence.term_months
        if kind == PunishmentType.CORRECTIONAL_LABOR.value and term is not None and term > MINOR_CORRECTIONAL_MAX_MONTHS:
            findings.append(f"исправительные работы {term:g} мес. — больше года (ч. 4)")
        if kind == IMPRISONMENT and term is not None:
            if found.imprisonment is None or found.imprisonment.max_months is None:
                unknown.append(f"предел лишения свободы по {found.norm.ref}")
            else:
                category = crime_category(float(found.imprisonment.max_months), context.applicable_at)
                young = age < YOUNG_MINOR_AGE
                maximum = MINOR_YOUNG_MAX_MONTHS if young and category.name != "особо тяжкое" else MINOR_MAX_MONTHS
                if term > maximum:
                    findings.append(f"лишение свободы {term:g} мес. — больше {maximum} мес. (ч. 6)")
                first_time = case.defendant.prior_convictions is False
                if case.defendant.prior_convictions is None:
                    unknown.append("сведения о прежних судимостях (впервые ли)")
                elif first_time and category.name == "небольшой тяжести":
                    findings.append("лишение свободы впервые осуждённому за преступление небольшой тяжести (ч. 6)")
                elif first_time and category.name == "средней тяжести":
                    if young:
                        findings.append("лишение свободы впервые осуждённому до 16 лет за преступление средней тяжести (ч. 6)")
                    else:
                        unknown.append("возраст на момент деяния: до 16 лет лишение свободы за среднюю тяжесть впервые не назначается (ч. 6)")
                minimum = found.imprisonment.min_months
                if category.name in ("тяжкое", "особо тяжкое") and minimum and term < minimum / 2:
                    findings.append(f"срок {term:g} мес. ниже половины минимума санкции ({minimum / 2:g} мес., ч. 6.1)")
        if findings:
            return self._evaluation(
                status=RuleStatus.WARNING,
                headline=f"{self.title}: проверьте ограничения",
                explanation="Подсудимому " + f"{age} лет; " + "; ".join(findings) + ". Обзор ВС РФ от 18.12.2024, п. 17.",
                facts_used=facts_used,
                norms_used=norms_used,
                missing=unknown,
            )
        if unknown:
            return self._unknown(unknown, f"Подсудимому {age} лет; часть ограничений ст. 88 проверить нечем.")
        return self._evaluation(
            status=RuleStatus.PASS,
            headline=f"{self.title}: ограничения соблюдены",
            explanation=f"Подсудимому {age} лет; вид и размер наказания в пределах ст. 88 УК РФ.",
            facts_used=facts_used,
            norms_used=norms_used,
        )


class RetroactivityRule(LegalRule):
    """Последующая редакция смягчила санкцию — повод применить ст. 10 УК РФ."""

    rule_id = "R-D06"
    version = "0.1.0-draft"
    title = "Обратная сила уголовного закона (ст. 10 УК РФ)"
    description = (
        "Сравнивает пределы лишения свободы в редакции на дату деяния и в редакциях, вступивших "
        "после неё до даты проверки. Другие способы улучшить положение лица (исключение признака, "
        "декриминализация) не проверяются."
    )
    norm_refs = ["uk-rf:art-10"]

    def evaluate(self, context) -> RuleEvaluation:
        found = _sanction(context)
        if isinstance(found, list):
            return self._unknown(found, "Без нормы на дату деяния сравнивать не с чем.")
        if found.imprisonment is None or found.imprisonment.max_months is None:
            return self._unknown([f"предел лишения свободы по {found.norm.ref}"], "Санкция не описана числом.")
        later = [
            v for v in context.norm_store.versions(found.norm.norm_id)
            if v.effective_from > context.applicable_at and v.effective_from <= date.today()
        ]
        norms_used = _refs(context, found.version, found.norm, "uk-rf:art-10", "УК РФ ст. 10")
        before = found.imprisonment
        milder = []
        for version in later:
            after = sanction_for(version.sanctions, IMPRISONMENT, version.effective_from)
            if after is None or after.max_months is None:
                continue
            lower_max = after.max_months < (before.max_months or 0)
            lower_min = (before.min_months or 0) > (after.min_months or 0)
            if lower_max or lower_min:
                milder.append(
                    f"редакция {version.version_id} с {version.effective_from.isoformat()}: "
                    f"максимум {after.max_months:g} мес. вместо {before.max_months:g}"
                    + (f", минимум {after.min_months or 0:g} вместо {before.min_months:g}" if lower_min and before.min_months else "")
                )
        if milder:
            return self._evaluation(
                status=RuleStatus.WARNING,
                headline=f"{self.title}: после деяния санкция смягчена",
                explanation="; ".join(milder) + ". Проверьте применение ст. 10 УК РФ (Обзор ВС РФ от 18.12.2024, п. 12).",
                norms_used=norms_used,
            )
        if not later and found.version.effective_to is None and not found.norm.history_verified:
            return self._unknown(
                [f"сверенная история редакций {found.norm.ref}"],
                "После даты деяния в корпусе редакций нет, но история нормы не сверена: изменение могло быть пропущено.",
            )
        return self._evaluation(
            status=RuleStatus.PASS,
            headline=f"{self.title}: смягчения санкции не найдено",
            explanation="Редакции после даты деяния не снижают пределы лишения свободы.",
            norms_used=norms_used,
        )


DRAFT_RULES = (RecidivismMinimumRule, MinorSentenceRule, RetroactivityRule)

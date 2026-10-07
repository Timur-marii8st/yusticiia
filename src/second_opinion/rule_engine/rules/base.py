"""Общее основание правил: константы, помощники и базовый класс.

Разрезано на модули по предмету правила: один файл — одна тема, а не
семьсот строк, в которых норма и её проверка теряются. Логика правил
не менялась; регрессионные сценарии (39 штук) держат это утверждение.
"""

from __future__ import annotations

from ...domain.enums import FactType, PunishmentType, RuleStatus
from ...domain.facts import CaseFacts, LegalFact
from ...domain.norms import NormRef, NormVersion
from ...domain.rules import LegalRule, RuleEvaluation
from ...legal_sources.store import NoApplicableVersionError, NormNotFoundError

REFERENCED_NORM_IDS = frozenset(
    {
        "uk-rf:art-60",
        "uk-rf:art-61",
        "uk-rf:art-62",
        "uk-rf:art-64",
        "uk-rf:art-66",
        "uk-rf:art-73",
        "upk-rf:art-316",
        "upk-rf:art-420",
        "ppvs-rf:n60-p7",
        "ppvs-rf:n60-p14",
        "uk-rf:art-75",
        "uk-rf:art-76",
        "uk-rf:art-76-2",
    }
)

DRAFT_ONLY_NORM_IDS = frozenset({"uk-rf:art-75", "uk-rf:art-76", "uk-rf:art-76-2"})

IMPRISONMENT = PunishmentType.IMPRISONMENT.value
MITIGATING_IK_CODES = {"61.1.и", "61.1.к"}
SUSPENDED_MAX_MONTHS = 8 * 12


def _facts_of_type(case_facts: CaseFacts, fact_type: FactType) -> list[LegalFact]:
    return [f for f in case_facts.facts if f.type is fact_type]


def _versioned_ref(context, norm_id: str, fallback_ref: str) -> NormRef:
    """Ссылка на норму с конкретной редакцией; при недоступности хранилища —
    ссылка без версии (честно помечена пустым version_id)."""
    try:
        version = context.norm_store.get_norm(norm_id, context.applicable_at)
        meta = context.norm_store.get_norm_meta(norm_id)
    except (NoApplicableVersionError, NormNotFoundError):
        return NormRef(norm_id=norm_id, version_id="", ref=fallback_ref)
    return NormRef(norm_id=norm_id, version_id=version.version_id, ref=meta.ref)


class _BaseSanctionRule(LegalRule):
    """Общая логика правил, сравнивающих срок с пределом санкции статьи."""

    def _prepare(self, context) -> tuple[RuleEvaluation | None, dict]:
        """Вернуть (досрочная оценка | None, служебные данные)."""
        case = context.case_facts
        missing: list[str] = []

        if case.sentence.punishment_type is None and case.primary_qualification is not None:
            return (
                self._unknown(
                    ["вид назначенного наказания"],
                    "Квалификация извлечена, а вид назначенного наказания — нет "
                    "(например, штраф или иной вид, которые система не "
                    "распознаёт). Предел санкции не проверялся: проверьте "
                    "документ вручную.",
                ),
                {},
            )
        if case.sentence.punishment_type != IMPRISONMENT:
            return (
                self._evaluation(
                    status=RuleStatus.NOT_APPLICABLE,
                    headline=f"{self.title}: не применимо",
                    explanation=(
                        "Назначенный вид наказания не связан с лишением свободы; "
                        "проверяемое ограничение в текущей редакции не применяется "
                        "к иным видам наказания без отдельных данных о их пределах."
                    ),
                ),
                {},
            )
        if case.sentence.term_months is None:
            missing.append("размер назначенного наказания (в месяцах)")
        qualification = case.primary_qualification
        if qualification is None:
            missing.append("квалификация (статья УК РФ)")
        if missing:
            return (
                self._unknown(
                    missing,
                    "Для проверки не хватает извлечённых данных. Система не делает "
                    "предположений: проверьте документ вручную.",
                ),
                {},
            )

        norm = context.norm_store.find_by_article(
            qualification.code,
            qualification.article,
            qualification.part,
            qualification.article_suffix,
        )
        if norm is None:
            return (
                self._unknown(
                    [f"норма для {qualification.ref} в базе источников не найдена"],
                    "Санкция статьи отсутствует в базе источников. Система не "
                    "выдумывает норму: добавьте источник или проверьте вручную.",
                ),
                {},
            )
        try:
            version = context.norm_store.get_norm(norm.norm_id, context.applicable_at)
        except (NoApplicableVersionError, NormNotFoundError):
            return (
                self._unknown(
                    [f"редакция {norm.ref} на дату {context.applicable_at.isoformat()}"],
                    "Не найдена редакция нормы, действовавшая на юридически значимую "
                    "дату. Система не применяет редакцию молча.",
                ),
                {},
            )
        sanction = next(
            (s for s in version.sanctions if s.punishment_type.value == IMPRISONMENT),
            None,
        )
        if sanction is None or sanction.max_months is None:
            return (
                self._unknown(
                    [f"максимум лишения свободы по {norm.ref}"],
                    "В данных санкции нет предела лишения свободы. Проверьте текст "
                    "нормы вручную.",
                ),
                {"norm": norm, "version": version},
            )
        return None, {
            "case": case,
            "norm": norm,
            "version": version,
            "max_months": sanction.max_months,
            "qualification": qualification,
        }

    @staticmethod
    def _norm_refs(version: NormVersion, ref: str) -> list[NormRef]:
        return [NormRef(norm_id=version.norm_id, version_id=version.version_id, ref=ref)]

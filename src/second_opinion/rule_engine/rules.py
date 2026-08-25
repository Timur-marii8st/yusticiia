from __future__ import annotations

from ..domain.enums import FactType, OffenseStage, PunishmentType, RuleStatus
from ..domain.facts import CaseFacts, LegalFact
from ..domain.norms import NormRef, NormVersion
from ..domain.rules import LegalRule, RuleEvaluation
from ..legal_sources.store import NoApplicableVersionError, NormNotFoundError

IMPRISONMENT = PunishmentType.IMPRISONMENT.value
MITIGATING_IK_CODES = {"61.1.и", "61.1.к"}
SUSPENDED_MAX_MONTHS = 8 * 12  # ст. 73 ч. 3 УК РФ


def _facts_of_type(case_facts: CaseFacts, fact_type: FactType) -> list[LegalFact]:
    return [f for f in case_facts.facts if f.type is fact_type]


class _BaseSanctionRule(LegalRule):
    """Общая логика правил, сравнивающих срок с пределом санкции статьи."""

    def _prepare(self, context) -> tuple[RuleEvaluation | None, dict]:
        """Вернуть (досрочная оценка | None, служебные данные)."""
        case = context.case_facts
        missing: list[str] = []

        if case.sentence.punishment_type != IMPRISONMENT:
            return (
                self._evaluation(
                    status=RuleStatus.PASS,
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
            qualification.code, qualification.article, qualification.part
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
        sanction_min = next(
            (
                s.min_months
                for s in version.sanctions
                if s.punishment_type.value == IMPRISONMENT
            ),
            None,
        )
        if sanction_min and term < sanction_min:
            return self._evaluation(
                status=RuleStatus.WARNING,
                headline=f"{self.title}: срок ниже минимума санкции",
                explanation=(
                    f"Назначено {term:.0f} мес. при минимуме санкции "
                    f"{sanction_min:.0f} мес. Назначение ниже низшего предела "
                    "возможно только по основаниям ст. 64 УК РФ — проверьте, "
                    "указаны ли они в акте."
                ),
                facts_used=facts_used,
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


class StageLimitRule(_BaseSanctionRule):
    """Пределы при неоконченном преступлении: покушение ≤ 3/4, приготовление ≤ 1/2 (ст. 66 УК РФ)."""

    rule_id = "R-002"
    version = "1.0.0"
    title = "Предел при неоконченном преступлении (ст. 66 УК РФ)"
    description = (
        "Срок за покушение не может превышать 3/4, за приготовление — 1/2 "
        "максимального срока наиболее строгого вида наказания."
    )
    norm_refs = ["uk-rf:art-66"]

    _FRACTIONS = {
        OffenseStage.ATTEMPT.value: (0.75, "покушение"),
        OffenseStage.PREPARATION.value: (0.5, "приготовление"),
    }

    def evaluate(self, context) -> RuleEvaluation:
        case: CaseFacts = context.case_facts
        stage = case.offense.stage
        if stage not in self._FRACTIONS:
            return self._evaluation(
                status=RuleStatus.PASS,
                headline=f"{self.title}: не применимо",
                explanation="Преступление квалифицировано как оконченное; предел ст. 66 УК РФ не применяется.",
            )
        early, data = self._prepare(context)
        if early is not None:
            return early
        fraction, stage_title = self._FRACTIONS[stage]
        limit = data["max_months"] * fraction
        term = float(case.sentence.term_months or 0)
        norms_used = self._norm_refs(data["version"], data["norm"].ref) + [
            NormRef(norm_id="uk-rf:art-66", version_id="", ref="УК РФ ст. 66")
        ]
        facts_used = [
            f.id
            for f in _facts_of_type(case, FactType.OFFENSE_STAGE)
            + _facts_of_type(case, FactType.PUNISHMENT_TERM)
            + _facts_of_type(case, FactType.QUALIFICATION)
        ]
        numbers = {
            "term_months": term,
            "max_months": data["max_months"],
            "limit_months": limit,
        }
        if term > limit:
            return self._evaluation(
                status=RuleStatus.FAIL,
                headline=f"{self.title}: возможно нарушение предела",
                explanation=(
                    f"Стадия — {stage_title}; назначено {term:.0f} мес., что выше "
                    f"предела {limit:.0f} мес. ({fraction:.0%} от максимума санкции "
                    f"{data['max_months']:.0f} мес.)."
                ),
                facts_used=facts_used,
                norms_used=norms_used,
                numbers=numbers,
            )
        return self._evaluation(
            status=RuleStatus.PASS,
            headline=f"{self.title}: предел соблюдён",
            explanation=(
                f"Стадия — {stage_title}; назначено {term:.0f} мес. при пределе "
                f"{limit:.0f} мес. ({fraction:.0%} от {data['max_months']:.0f} мес.)."
            ),
            facts_used=facts_used,
            norms_used=norms_used,
            numbers=numbers,
        )


class SpecialProcedureLimitRule(_BaseSanctionRule):
    """Особый порядок: срок не более 2/3 максимума санкции (ст. 62 ч. 2 УК РФ)."""

    rule_id = "R-003"
    version = "1.0.0"
    title = "Предел при особом порядке (ст. 62 ч. 2 УК РФ)"
    description = (
        "При постановлении приговора в особом порядке (гл. 40 УПК РФ) наказание "
        "не может превышать 2/3 максимального срока наиболее строгого вида наказания."
    )
    norm_refs = ["uk-rf:art-62"]

    def evaluate(self, context) -> RuleEvaluation:
        case: CaseFacts = context.case_facts
        if case.procedural.special_procedure is None:
            return self._unknown(
                ["признак рассмотрения дела в особом порядке"],
                "Из документа не удалось достоверно извлечь признак особого порядка. "
                "Проверьте раздел о порядке судопроизводства.",
            )
        if not case.procedural.special_procedure:
            return self._evaluation(
                status=RuleStatus.PASS,
                headline=f"{self.title}: не применимо",
                explanation="Дело рассмотрено не в особом порядке; ограничение ст. 62 ч. 2 УК РФ не применяется.",
            )
        early, data = self._prepare(context)
        if early is not None:
            return early
        limit = data["max_months"] * 2 / 3
        term = float(case.sentence.term_months or 0)
        norms_used = self._norm_refs(data["version"], data["norm"].ref) + [
            NormRef(norm_id="uk-rf:art-62", version_id="", ref="УК РФ ст. 62 ч. 2")
        ]
        facts_used = [
            f.id
            for f in _facts_of_type(case, FactType.SPECIAL_PROCEDURE)
            + _facts_of_type(case, FactType.PUNISHMENT_TERM)
            + _facts_of_type(case, FactType.QUALIFICATION)
        ]
        numbers = {
            "term_months": term,
            "max_months": data["max_months"],
            "limit_months": limit,
        }
        if term > limit:
            return self._evaluation(
                status=RuleStatus.FAIL,
                headline=f"{self.title}: возможно нарушение предела",
                explanation=(
                    f"Особый порядок: назначено {term:.0f} мес. при пределе "
                    f"{limit:.0f} мес. (2/3 от {data['max_months']:.0f} мес.)."
                ),
                facts_used=facts_used,
                norms_used=norms_used,
                numbers=numbers,
            )
        return self._evaluation(
            status=RuleStatus.PASS,
            headline=f"{self.title}: предел соблюдён",
            explanation=(
                f"Особый порядок: назначено {term:.0f} мес. при пределе {limit:.0f} мес."
            ),
            facts_used=facts_used,
            norms_used=norms_used,
            numbers=numbers,
        )


class MitigatingTwoThirdsRule(_BaseSanctionRule):
    """Смягчающие пп. «и»/«к» без отягчающих: срок не более 2/3 максимума (ст. 62 ч. 1 УК РФ)."""

    rule_id = "R-004"
    version = "1.0.0"
    title = "Предел при смягчающих пп. «и»/«к» (ст. 62 ч. 1 УК РФ)"
    description = (
        "При наличии смягчающих обстоятельств пп. «и» и (или) «к» ч. 1 ст. 61 УК РФ "
        "и отсутствии отягчающих наказание не может превышать 2/3 максимума санкции."
    )
    norm_refs = ["uk-rf:art-62", "uk-rf:art-61"]

    def evaluate(self, context) -> RuleEvaluation:
        case: CaseFacts = context.case_facts
        has_ik = bool(case.mitigating_codes() & MITIGATING_IK_CODES)
        if not has_ik:
            return self._evaluation(
                status=RuleStatus.PASS,
                headline=f"{self.title}: не применимо",
                explanation=(
                    "Смягчающие обстоятельства пп. «и»/«к» ч. 1 ст. 61 УК РФ "
                    "в извлечённых фактах не обнаружены."
                ),
            )
        if case.has_aggravating:
            return self._evaluation(
                status=RuleStatus.PASS,
                headline=f"{self.title}: не применимо из-за отягчающих",
                explanation=(
                    "Имеются отягчающие обстоятельства; при них предел ст. 62 ч. 1 УК РФ не действует."
                ),
                facts_used=[
                    factor_id
                    for aggravating in case.aggravating
                    for factor_id in aggravating.fact_ids
                ],
            )
        early, data = self._prepare(context)
        if early is not None:
            return early
        limit = data["max_months"] * 2 / 3
        term = float(case.sentence.term_months or 0)
        norms_used = self._norm_refs(data["version"], data["norm"].ref) + [
            NormRef(norm_id="uk-rf:art-62", version_id="", ref="УК РФ ст. 62 ч. 1"),
            NormRef(norm_id="uk-rf:art-61", version_id="", ref="УК РФ ст. 61 ч. 1"),
        ]
        mitigating_fact_ids = [
            fact_id
            for mitigating in case.mitigating
            if mitigating.code in MITIGATING_IK_CODES
            for fact_id in mitigating.fact_ids
        ]
        facts_used = mitigating_fact_ids + [
            f.id
            for f in _facts_of_type(case, FactType.PUNISHMENT_TERM)
            + _facts_of_type(case, FactType.QUALIFICATION)
        ]
        numbers = {
            "term_months": term,
            "max_months": data["max_months"],
            "limit_months": limit,
        }
        if term > limit:
            return self._evaluation(
                status=RuleStatus.FAIL,
                headline=f"{self.title}: возможно нарушение предела",
                explanation=(
                    f"Назначено {term:.0f} мес. при пределе {limit:.0f} мес. "
                    "(2/3 максимума санкции) — при наличии смягчающих "
                    "пп. «и»/«к» и отсутствии отягчающих."
                ),
                facts_used=facts_used,
                norms_used=norms_used,
                numbers=numbers,
            )
        return self._evaluation(
            status=RuleStatus.PASS,
            headline=f"{self.title}: предел соблюдён",
            explanation=f"Назначено {term:.0f} мес. при пределе {limit:.0f} мес.",
            facts_used=facts_used,
            norms_used=norms_used,
            numbers=numbers,
        )


class CombinedLimitRule(_BaseSanctionRule):
    """Одновременно ч. 1 и ч. 2 ст. 62 УК РФ: срок не более 1/3 максимума (ч. 3 ст. 62)."""

    rule_id = "R-005"
    version = "1.0.0"
    title = "Совокупный предел ст. 62 ч. 1 и ч. 2 УК РФ"
    description = (
        "При одновременном наличии условий ч. 1 и ч. 2 ст. 62 УК РФ наказание "
        "не может превышать 1/3 максимума санкции (ч. 3 ст. 62 УК РФ)."
    )
    norm_refs = ["uk-rf:art-62"]

    def evaluate(self, context) -> RuleEvaluation:
        case: CaseFacts = context.case_facts
        has_ik = bool(case.mitigating_codes() & MITIGATING_IK_CODES)
        if not (case.procedural.special_procedure and has_ik and not case.has_aggravating):
            return self._evaluation(
                status=RuleStatus.PASS,
                headline=f"{self.title}: не применимо",
                explanation=(
                    "Условия ч. 1 и ч. 2 ст. 62 УК РФ не выполняются одновременно; "
                    "предел ч. 3 ст. 62 УК РФ не применяется."
                ),
            )
        early, data = self._prepare(context)
        if early is not None:
            return early
        limit = data["max_months"] / 3
        term = float(case.sentence.term_months or 0)
        norms_used = self._norm_refs(data["version"], data["norm"].ref) + [
            NormRef(norm_id="uk-rf:art-62", version_id="", ref="УК РФ ст. 62 ч. 3")
        ]
        facts_used = [
            f.id
            for f in _facts_of_type(case, FactType.SPECIAL_PROCEDURE)
            + _facts_of_type(case, FactType.PUNISHMENT_TERM)
            + _facts_of_type(case, FactType.QUALIFICATION)
        ] + [
            fact_id
            for mitigating in case.mitigating
            if mitigating.code in MITIGATING_IK_CODES
            for fact_id in mitigating.fact_ids
        ]
        numbers = {
            "term_months": term,
            "max_months": data["max_months"],
            "limit_months": limit,
        }
        if term > limit:
            return self._evaluation(
                status=RuleStatus.FAIL,
                headline=f"{self.title}: возможно нарушение предела",
                explanation=(
                    f"Особый порядок + смягчающие пп. «и»/«к»: назначено {term:.0f} мес. "
                    f"при пределе {limit:.0f} мес. (1/3 от {data['max_months']:.0f} мес.)."
                ),
                facts_used=facts_used,
                norms_used=norms_used,
                numbers=numbers,
            )
        return self._evaluation(
            status=RuleStatus.PASS,
            headline=f"{self.title}: предел соблюдён",
            explanation=f"Назначено {term:.0f} мес. при пределе {limit:.0f} мес.",
            facts_used=facts_used,
            norms_used=norms_used,
            numbers=numbers,
        )


class SuspendedLimitRule(LegalRule):
    """Условное осуждение возможно при лишении свободы до 8 лет (ст. 73 ч. 3 УК РФ)."""

    rule_id = "R-006"
    version = "1.0.0"
    title = "Предел условного осуждения (ст. 73 ч. 3 УК РФ)"
    description = (
        "Условное осуждение назначается при лишении свободы на срок до 8 лет."
    )
    norm_refs = ["uk-rf:art-73"]

    def evaluate(self, context) -> RuleEvaluation:
        case: CaseFacts = context.case_facts
        norm_ref = NormRef(norm_id="uk-rf:art-73", version_id="", ref="УК РФ ст. 73 ч. 3")
        if case.sentence.suspended is None:
            return self._unknown(
                ["признак условного осуждения"],
                "Не удалось извлечь, назначено ли условное осуждение. "
                "Проверьте резолютивную часть.",
            )
        if not case.sentence.suspended:
            return self._evaluation(
                status=RuleStatus.PASS,
                headline=f"{self.title}: не применимо",
                explanation="Условное осуждение не назначалось.",
            )
        if case.sentence.term_months is None:
            return self._unknown(
                ["размер назначенного наказания"],
                "Условное осуждение обнаружено, но срок наказания не извлечён.",
            )
        term = float(case.sentence.term_months)
        facts_used = [
            f.id
            for f in _facts_of_type(case, FactType.SUSPENDED_SENTENCE)
            + _facts_of_type(case, FactType.PUNISHMENT_TERM)
        ]
        numbers = {"term_months": term, "limit_months": float(SUSPENDED_MAX_MONTHS)}
        if term > SUSPENDED_MAX_MONTHS:
            return self._evaluation(
                status=RuleStatus.FAIL,
                headline=f"{self.title}: возможно нарушение",
                explanation=(
                    f"Условное осуждение при сроке {term:.0f} мес. превышает предел "
                    f"{SUSPENDED_MAX_MONTHS} мес. (8 лет)."
                ),
                facts_used=facts_used,
                norms_used=[norm_ref],
                numbers=numbers,
            )
        return self._evaluation(
            status=RuleStatus.PASS,
            headline=f"{self.title}: предел соблюдён",
            explanation=(
                f"Условное осуждение при сроке {term:.0f} мес. — в пределах "
                f"{SUSPENDED_MAX_MONTHS} мес. (8 лет)."
            ),
            facts_used=facts_used,
            norms_used=[norm_ref],
            numbers=numbers,
        )

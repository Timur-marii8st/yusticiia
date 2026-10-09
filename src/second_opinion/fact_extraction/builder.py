from __future__ import annotations

from ..domain.enums import FactType, OffenseStage
from ..domain.facts import (
    AggravatingFactor,
    CaseFacts,
    LegalFact,
    MitigatingFactor,
    Qualification,
)

_ROLE_RANK = {"organized_group": 2, "group_with_conspiracy": 1, "group_of_persons": 0}


def build_case_facts(facts: list[LegalFact]) -> CaseFacts:
    """Собрать агрегат обстоятельств дела из реестра фактов."""
    case = CaseFacts(facts=list(facts))

    for fact in facts:
        value = fact.value
        if fact.type is FactType.QUALIFICATION and isinstance(value, dict):
            case.offense.qualifications.append(
                Qualification(
                    code=value.get("code", "УК РФ"),
                    article=int(value["article"]),
                    article_suffix=value.get("article_suffix"),
                    part=value.get("part"),
                )
            )
        elif fact.type is FactType.DEFENDANT_AGE:
            case.defendant.age = int(value)
        elif fact.type is FactType.PRIOR_CONVICTIONS:
            case.defendant.prior_convictions = bool(value)
        elif fact.type is FactType.MINOR_DEPENDENTS:
            case.defendant.minor_dependents = bool(value)
            if value:
                case.mitigating.append(
                    MitigatingFactor(
                        code="61.2",
                        title="Наличие несовершеннолетних детей "
                        "(учитывается по ч. 2 ст. 61 УК РФ; для п. «г» ч. 1 проверьте малолетность)",
                        fact_ids=[fact.id],
                    )
                )
        elif fact.type is FactType.HEALTH_FACTOR:
            case.defendant.health_factors.append(str(value))
        elif fact.type is FactType.OFFENSE_STAGE:
            case.offense.stage = str(value)
        elif fact.type is FactType.GROUP_OFFENSE:
            role = str(value)
            if _ROLE_RANK[role] > _ROLE_RANK.get(case.offense.complicity_role or "", 0):
                case.offense.complicity_role = role
        elif fact.type is FactType.GUILTY_PLEA:
            case.procedural.guilty_plea = bool(value)
        elif fact.type is FactType.SURRENDER_OR_CONFESSION:
            if value:
                case.mitigating.append(
                    MitigatingFactor(
                        code="61.1.и",
                        title="Явка с повинной / активное способствование раскрытию "
                        "(п. «и» ч. 1 ст. 61 УК РФ)",
                        fact_ids=[fact.id],
                    )
                )
        elif fact.type is FactType.RESTITUTION:
            if value:
                case.mitigating.append(
                    MitigatingFactor(
                        code="61.1.к",
                        title="Добровольное возмещение ущерба / заглаживание вреда "
                        "(п. «к» ч. 1 ст. 61 УК РФ)",
                        fact_ids=[fact.id],
                    )
                )
        elif fact.type is FactType.AGGRAVATING_RECIDIVISM:
            if value:
                case.aggravating.append(
                    AggravatingFactor(
                        code="63.1.а",
                        title="Рецидив преступлений (п. «а» ч. 1 ст. 63 УК РФ)",
                        fact_ids=[fact.id],
                    )
                )
        elif fact.type is FactType.SPECIAL_PROCEDURE:
            case.procedural.special_procedure = bool(value)
        elif fact.type is FactType.JURY_TRIAL:
            case.procedural.jury_trial = bool(value)
        elif fact.type is FactType.PUNISHMENT_TYPE:
            case.sentence.punishment_type = str(value)
        elif fact.type is FactType.PUNISHMENT_TERM:
            case.sentence.term_months = float(value)
        elif fact.type is FactType.FINAL_PUNISHMENT_TERM:
            case.sentence.final_term_months = float(value)
        elif fact.type is FactType.PUNISHMENT_HOURS:
            case.sentence.hours = float(value)
        elif fact.type is FactType.FINE_AMOUNT:
            case.sentence.fine_rubles = float(value)
        elif fact.type is FactType.SUSPENDED_SENTENCE:
            case.sentence.suspended = bool(value)
        elif fact.type is FactType.DATE_OF_OFFENSE:
            case.applicable_at = str(value)

    if case.offense.stage is None:
        case.offense.stage = OffenseStage.COMPLETED.value
    return case

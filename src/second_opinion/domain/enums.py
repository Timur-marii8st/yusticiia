from __future__ import annotations

from enum import StrEnum


class FactStatus(StrEnum):
    """Статус достоверности извлечённого факта."""

    VERIFIED = "VERIFIED"
    LIKELY = "LIKELY"
    UNCERTAIN = "UNCERTAIN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"


class RuleStatus(StrEnum):
    """Результат проверки правила."""

    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class FactType(StrEnum):
    """Типы юридически значимых фактов (доменный срез MVP)."""

    QUALIFICATION = "qualification"
    DEFENDANT_AGE = "defendant_age"
    PRIOR_CONVICTIONS = "prior_convictions"
    MINOR_DEPENDENTS = "minor_dependents"
    HEALTH_FACTOR = "health_factor"
    OFFENSE_STAGE = "offense_stage"
    GROUP_OFFENSE = "group_offense"
    GUILTY_PLEA = "guilty_plea"
    SURRENDER_OR_CONFESSION = "surrender_or_confession"
    RESTITUTION = "restitution"
    AGGRAVATING_RECIDIVISM = "aggravating_recidivism"
    SPECIAL_PROCEDURE = "special_procedure"
    JURY_TRIAL = "jury_trial"
    PUNISHMENT_TYPE = "punishment_type"
    PUNISHMENT_TERM = "punishment_term"
    SUSPENDED_SENTENCE = "suspended_sentence"
    DATE_OF_OFFENSE = "date_of_offense"


class OffenseStage(StrEnum):
    COMPLETED = "completed"
    ATTEMPT = "attempt"
    PREPARATION = "preparation"


class PunishmentType(StrEnum):
    IMPRISONMENT = "imprisonment"
    FINE = "fine"
    CORRECTIONAL_LABOR = "correctional_labor"
    COMPULSORY_LABOR = "compulsory_labor"
    RESTRICTION_OF_LIBERTY = "restriction_of_liberty"
    OTHER = "other"


class ExtractionMethod(StrEnum):
    PATTERN = "pattern"
    LLM = "llm"
    USER = "user"


class VerificationStatus(StrEnum):
    DRAFT = "draft"
    VERIFIED = "verified"

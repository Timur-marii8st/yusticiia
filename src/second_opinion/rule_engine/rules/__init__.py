"""Правила движка, разложенные по предмету.

Ввозятся здесь, чтобы прежние импорты (`from .rules import …`) работали
без изменений: перестановка файлов не повод трогать вызывающий код.
"""

from .base import (
    IMPRISONMENT,
    MITIGATING_IK_CODES,
    REFERENCED_NORM_IDS,
    SUSPENDED_MAX_MONTHS,
)
from .mitigating import MitigatingTwoThirdsRule
from .procedure import MinorSpecialProcedureRule, SpecialProcedureLimitRule
from .sanction import SanctionRangeRule
from .sentence import SentenceCompletenessRule, SuspendedLimitRule
from .stage import SequentialLimitRule, StageLimitRule

__all__ = [
    "IMPRISONMENT",
    "MITIGATING_IK_CODES",
    "REFERENCED_NORM_IDS",
    "SUSPENDED_MAX_MONTHS",
    "MinorSpecialProcedureRule",
    "MitigatingTwoThirdsRule",
    "SanctionRangeRule",
    "SentenceCompletenessRule",
    "SequentialLimitRule",
    "SpecialProcedureLimitRule",
    "StageLimitRule",
    "SuspendedLimitRule",
]

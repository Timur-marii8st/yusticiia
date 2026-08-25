from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from .enums import RuleStatus
from .norms import NormRef


class RuleEvaluation(BaseModel):
    """Результат детерминированной юридической проверки."""

    rule_id: str
    rule_version: str
    status: RuleStatus
    headline: str
    explanation: str
    facts_used: list[str] = Field(default_factory=list)
    norms_used: list[NormRef] = Field(default_factory=list)
    numbers: dict[str, float] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)


class LegalRule(ABC):
    """Формализованная юридическая проверка (детерминированная).

    Правило НЕ принимает решений о наказании: оно проверяет соблюдение
    конкретного нормативного ограничения и сообщает результат со статусом
    ``PASS / WARNING / FAIL / UNKNOWN``.
    """

    rule_id: str
    version: str
    title: str
    description: str
    norm_refs: list[str]  # norm_id норм, на которых основано правило

    @abstractmethod
    def evaluate(self, context: Any) -> RuleEvaluation:
        """Выполнить проверку над контекстом анализа."""

    def _unknown(self, missing: list[str], explanation: str) -> RuleEvaluation:
        return RuleEvaluation(
            rule_id=self.rule_id,
            rule_version=self.version,
            status=RuleStatus.UNKNOWN,
            headline=f"{self.title}: недостаточно данных",
            explanation=explanation,
            missing=missing,
        )

    def _evaluation(self, **kwargs: Any) -> RuleEvaluation:
        kwargs.setdefault("rule_id", self.rule_id)
        kwargs.setdefault("rule_version", self.version)
        return RuleEvaluation(**kwargs)

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..domain.facts import CaseFacts
from ..legal_sources.store import NormStore


@dataclass(frozen=True)
class RuleContext:
    """Вход правило-движка: обстоятельства дела + применимые нормы."""

    case_facts: CaseFacts
    applicable_at: date
    norm_store: NormStore

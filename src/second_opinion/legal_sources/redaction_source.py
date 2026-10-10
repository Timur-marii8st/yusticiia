"""Источник редакционной истории нормы.

Сегодня границы редакций в фикстурах проставляет юрист вручную, и аудит
(`coverage.py`) показывает, что у 15 норм история не зафиксирована вовсе.
Внешний источник может отдать сами границы — даты, с которых действовала
каждая редакция, и чем она введена.

Чего источник НЕ даёт и дать не может: ``sanctions`` — пределы наказания,
которые читает движок правил. Их выводит юрист из текста статьи. Поэтому
результат работы источника — не готовая ``NormVersion``, а **план
редакций**: границы и реквизиты, которые юристу остаётся наполнить
текстом и санкциями.

Интерфейс описан отдельно от реализаций, чтобы бизнес-логика не зависела
от конкретного поставщика (тот же приём, что в ``llm/provider.py``).
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from pydantic import BaseModel, Field


class RedactionSourceError(RuntimeError):
    """Источник недоступен или ответил не по контракту."""


class ChangingDocument(BaseModel):
    """Документ, которым внесено изменение."""

    topic: str = ""
    text: str = ""
    entry: str = ""


class UncertainPeriod(BaseModel):
    """Период, для которого источник не уверен в действии редакции.

    Хранится и показывается наравне с остальным: скрывать неопределённость
    запрещено.
    """

    valid_from: date | None = None
    valid_to: date | None = None
    description: str = ""


class Redaction(BaseModel):
    """Одна редакция документа в трактовке источника."""

    valid_from: date
    valid_to: date | None = None
    status: str = ""
    changing_documents: list[ChangingDocument] = Field(default_factory=list)


class DocumentRedactions(BaseModel):
    """Редакционная история документа целиком."""

    topic: str
    source: str = Field(description="имя поставщика, откуда получены данные")
    redactions: list[Redaction] = Field(default_factory=list)
    uncertain: list[UncertainPeriod] = Field(default_factory=list)
    live: bool = True

    def sorted_redactions(self) -> list[Redaction]:
        return sorted(self.redactions, key=lambda r: r.valid_from)


class PlannedVersion(BaseModel):
    """Заготовка редакции для фикстуры: границы есть, содержания нет."""

    version_id: str
    effective_from: date
    effective_to: date | None = None
    introduced_by: str = ""
    note: str = ""


class RedactionSource(Protocol):
    """Поставщик редакционной истории."""

    name: str

    def redactions(self, topic: str) -> DocumentRedactions:
        """Вернуть историю редакций документа."""
        ...


def plan_versions(history: DocumentRedactions) -> list[PlannedVersion]:
    """Превратить историю источника в заготовки редакций.

    Идентификатор — по году начала действия, как в существующих фикстурах
    (`v-1996`, `v-2003`). Совпадение годов разводится суффиксом, иначе
    ``NormStore`` отклонит норму с повторяющимися идентификаторами.
    """
    planned: list[PlannedVersion] = []
    used: dict[str, int] = {}

    for redaction in history.sorted_redactions():
        year = redaction.valid_from.year
        base = f"v-{year}"
        used[base] = used.get(base, 0) + 1
        version_id = base if used[base] == 1 else f"{base}-{used[base]}"

        introduced = "; ".join(
            d.text for d in redaction.changing_documents if d.text
        )
        note = "" if history.live else (
            "ЗАГЛУШКА: данные не получены от источника, проверять нельзя"
        )
        planned.append(
            PlannedVersion(
                version_id=version_id,
                effective_from=redaction.valid_from,
                effective_to=redaction.valid_to,
                introduced_by=introduced,
                note=note,
            )
        )
    return planned

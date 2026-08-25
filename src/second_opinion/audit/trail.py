from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field


class AuditEvent(BaseModel):
    """Запись аудит-журнала. Не содержит сырых текстов документов."""

    timestamp: str
    request_id: str
    operation: str
    component: str
    input_hash: str = ""
    output_hash: str = ""
    model: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None
    rule_version: str | None = None
    sources: list[str] = Field(default_factory=list)
    details: dict = Field(default_factory=dict)


class AuditTrail:
    """JSONL-журнал операций для воспроизводимости отчётов.

    Принцип: логируются идентификаторы, хэши и версии — не содержимое
    документов (см. docs/LEGAL_SAFETY_PRINCIPLES.md §8).
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._recent: list[AuditEvent] = []
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        operation: str,
        component: str,
        request_id: str = "-",
        **fields: object,
    ) -> AuditEvent:
        event = AuditEvent(
            timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
            request_id=request_id,
            operation=operation,
            component=component,
            **fields,  # type: ignore[arg-type]
        )
        with self._lock:
            self._recent.append(event)
            if len(self._recent) > 1000:
                self._recent = self._recent[-500:]
            if self._path is not None:
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(event.model_dump_json() + "\n")
        return event

    @property
    def recent(self) -> list[AuditEvent]:
        with self._lock:
            return list(self._recent)

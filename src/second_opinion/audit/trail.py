from __future__ import annotations

import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_MAX_BYTES = 64 * 1024 * 1024

ANONYMOUS_ACTOR = "anonymous"
SHARED_TOKEN_ACTOR = "shared-token"
CLI_ACTOR = "cli"


class AuditEvent(BaseModel):
    """Запись аудит-журнала. Не содержит сырых текстов документов."""

    timestamp: str
    request_id: str
    operation: str
    component: str
    actor: str = "anonymous"
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
    документов.
    """

    def __init__(self, path: Path | None = None, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._path = path
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._recent: list[AuditEvent] = []
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def _rotate_if_needed(self) -> None:
        """Закрыть разросшийся журнал и начать новый.

        Старый файл переименовывается с отметкой времени и остаётся на
        месте: записи аудита не удаляются и не перезаписываются — это
        провенанс, а не отладочный лог.
        """
        if self._path is None or self._max_bytes <= 0:
            return
        try:
            size = self._path.stat().st_size
        except FileNotFoundError:
            return
        if size < self._max_bytes:
            return
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        rotated = self._path.with_name(f"{self._path.stem}-{stamp}{self._path.suffix}")
        serial = 2
        while rotated.exists():
            rotated = self._path.with_name(
                f"{self._path.stem}-{stamp}-{serial}{self._path.suffix}"
            )
            serial += 1
        os.replace(self._path, rotated)

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
                self._rotate_if_needed()
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(event.model_dump_json() + "\n")
        return event

    @property
    def recent(self) -> list[AuditEvent]:
        with self._lock:
            return list(self._recent)

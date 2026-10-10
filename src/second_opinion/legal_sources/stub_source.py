"""Заглушка источника редакций: ответы из подготовленных файлов.

Нужна, пока токен ГАРАНТ не получен: позволяет писать и проверять всё,
кроме самого сетевого вызова.

Файлы заглушки — **предположение по документации, а не записанный ответ
сервера**. Чтобы это нельзя было забыть, каждый файл обязан объявлять
``"assumed": true``; файл без такой отметки не загружается. Тот же
принцип, что и у ``synthetic: true`` в фикстурах дел.
"""

from __future__ import annotations

import json
from pathlib import Path

from .garant import parse_redactions
from .redaction_source import DocumentRedactions, RedactionSourceError

ASSUMPTION_MARKER = "assumed"


class StubRedactionSource:
    """Отдаёт редакционную историю из каталога файлов."""

    name = "stub"

    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)

    def _path(self, topic: str) -> Path:
        if not topic or "/" in topic or topic.startswith("."):
            raise RedactionSourceError(f"недопустимый идентификатор документа: {topic!r}")
        return self._dir / f"{topic}.json"

    def redactions(self, topic: str) -> DocumentRedactions:
        path = self._path(topic)
        if not path.exists():
            raise RedactionSourceError(
                f"заглушка не содержит документа {topic}: нет файла {path}"
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get(ASSUMPTION_MARKER) is not True:
            raise RedactionSourceError(
                f"{path}: файл заглушки обязан объявлять \"{ASSUMPTION_MARKER}\": true — "
                "иначе предположение по документации однажды примут за данные сервера"
            )
        history = parse_redactions(topic, payload.get("response"))
        return history.model_copy(update={"source": self.name, "live": False})

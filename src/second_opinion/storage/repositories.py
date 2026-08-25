from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def _atomic_write_json(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise


class JsonFileRepository(Generic[T]):
    """Простой файловый репозиторий: один объект — один JSON-файл.

    См. docs/ADR/ADR-006: реализация за интерфейсом; миграция на
    PostgreSQL выполняется заменой реализации.
    """

    def __init__(self, directory: Path, model: type[T], id_field: str) -> None:
        self._dir = directory
        self._model = model
        self._id_field = id_field
        self._lock = threading.Lock()
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, obj: T) -> T:
        obj_id = getattr(obj, self._id_field)
        with self._lock:
            _atomic_write_json(
                self._dir / f"{obj_id}.json", obj.model_dump_json(indent=2)
            )
        return obj

    def get(self, obj_id: str) -> T | None:
        path = self._dir / f"{obj_id}.json"
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as fh:
            return self._model.model_validate(json.load(fh))

    def list(self) -> list[T]:
        items: list[T] = []
        for path in sorted(self._dir.glob("*.json")):
            with path.open(encoding="utf-8") as fh:
                items.append(self._model.model_validate(json.load(fh)))
        return items

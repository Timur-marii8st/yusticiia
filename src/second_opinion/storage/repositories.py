from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class UnsafeIdentifierError(ValueError):
    """Идентификатор объекта выводит за пределы каталога хранилища."""


T = TypeVar("T", bound=BaseModel)


class Repository(Protocol[T]):
    """Хранилище объектов одного типа.

    ADR-006 обещает подменяемый носитель: файлы для MVP, PostgreSQL для
    эксплуатации. В типах этого обещания не было — конвейер требовал
    именно файловый класс, и подстановка Postgres держалась на честном
    слове. Протокол делает обещание проверяемым.
    """

    def save(self, obj: T) -> T: ...

    def get(self, obj_id: str) -> T | None: ...

    def list(self) -> list[T]: ...

    def delete(self, obj_id: str) -> bool: ...


def atomic_write_json(path: Path, payload: str) -> None:
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

    Реализация спрятана за интерфейсом: миграция на PostgreSQL
    выполняется заменой реализации, без изменения вызывающего кода.
    """

    def __init__(self, directory: Path, model: type[T], id_field: str) -> None:
        self._dir = directory
        self._model = model
        self._id_field = id_field
        self._lock = threading.Lock()
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, obj_id: str) -> Path | None:
        """Путь к файлу объекта; ``None`` — идентификатор небезопасен."""
        if not isinstance(obj_id, str) or not _SAFE_ID.match(obj_id):
            logger.warning(
                "отклонён небезопасный идентификатор объекта: %r", obj_id
            )
            return None
        return self._dir / f"{obj_id}.json"

    def save(self, obj: T) -> T:
        obj_id = getattr(obj, self._id_field)
        if self._path(obj_id) is None:
            raise UnsafeIdentifierError(
                f"идентификатор {obj_id!r} не может быть именем файла"
            )
        with self._lock:
            atomic_write_json(
                self._dir / f"{obj_id}.json", obj.model_dump_json(indent=2)
            )
        return obj

    def get(self, obj_id: str) -> T | None:
        path = self._path(obj_id)
        if path is None or not path.exists():
            return None
        with path.open(encoding="utf-8") as fh:
            return self._model.model_validate(json.load(fh))

    def list(self) -> list[T]:
        items: list[T] = []
        for path in sorted(self._dir.glob("*.json")):
            with path.open(encoding="utf-8") as fh:
                items.append(self._model.model_validate(json.load(fh)))
        return items

    def delete(self, obj_id: str) -> bool:
        """Удалить объект; вернуть True, если он существовал."""
        path = self._path(obj_id)
        if path is None:
            return False
        with self._lock:
            if not path.exists():
                return False
            path.unlink()
            return True

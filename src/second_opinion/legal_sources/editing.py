"""Правка нормативных фикстур: сверка истории и добавление редакций.

Аудит покрытия показывает, чего не хватает, но закрыть пробел до сих пор
можно было только правкой JSON вручную. Здесь два действия, которыми
юрист закрывает норму, не завязываясь ни на какой внешний API:

* подтвердить, что история изменений сверена (норма действительно не
  менялась) — тогда единственная редакция перестаёт быть допущением;
* добавить редакцию с фактическими границами, закрыв интервал предыдущей.

Обе операции оставляют корпус неопечатанным: опечатывание — отдельное
осознанное действие (``second-opinion norms-seal``).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .sealing import MANIFEST_NAME, UNSEALED


class NormEditError(ValueError):
    """Правка невозможна: норма не найдена или нарушает инварианты."""


def _iter_files(directory: Path):
    for path in sorted(Path(directory).glob("*.json")):
        if path.name != MANIFEST_NAME:
            yield path


def _find_norm(directory: Path, norm_id: str) -> tuple[Path, dict, dict]:
    """Найти файл и запись нормы по идентификатору."""
    for path in _iter_files(directory):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for norm in payload.get("norms", []):
            if norm.get("norm_id") == norm_id:
                return path, payload, norm
    raise NormEditError(f"норма не найдена: {norm_id}")


def _write(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def confirm_history(
    directory: Path,
    norm_id: str,
    *,
    checked_through: date,
    source: str,
    source_edition: str = "",
) -> Path:
    """Зафиксировать, что история изменений нормы сверена.

    Применимо, когда сверка подтвердила: статья действительно не
    менялась. Утверждение перестаёт быть допущением и норма уходит из
    списка на сверку.

    ``source_edition`` ставит точку отсчёта для обнаружения устаревания:
    какой редакции источника соответствует сверенный текст. Без неё
    ``norms-check-updates`` не может сказать о норме ничего — сравнивать
    не с чем. Отметка ставится только на действующие редакции: закрытый
    интервал описывает прошлое, и «устареть» оно не может.
    """
    if not source.strip():
        raise NormEditError(
            "укажите источник сверки: утверждение без источника ничем не лучше допущения"
        )
    path, payload, norm = _find_norm(Path(directory), norm_id)
    norm["amendments_checked_through"] = checked_through.isoformat()
    norm["amendments_checked_source"] = source.strip()

    if source_edition.strip():
        open_versions = [
            v for v in norm.get("versions", []) if v.get("effective_to") in (None, "")
        ]
        if not open_versions:
            raise NormEditError(
                f"у нормы {norm_id} нет действующей редакции: отмечать редакцию "
                "источника не на чем"
            )
        for version in open_versions:
            version["source_edition"] = source_edition.strip()

    _write(path, payload)
    return path


def add_version(
    directory: Path,
    norm_id: str,
    *,
    version_id: str,
    text: str,
    effective_from: date,
    effective_to: date | None = None,
    source_url: str = "",
    source_edition: str = "",
) -> Path:
    """Добавить редакцию, закрыв интервал предыдущей.

    Предыдущая открытая редакция закрывается днём до начала новой: два
    интервала не должны пересекаться, иначе ``NormStore`` отклонит норму.
    """
    if not text.strip():
        raise NormEditError("текст редакции пуст")
    path, payload, norm = _find_norm(Path(directory), norm_id)

    versions: list[dict] = norm.setdefault("versions", [])
    if any(v.get("version_id") == version_id for v in versions):
        raise NormEditError(
            f"редакция {version_id} у нормы {norm_id} уже есть"
        )

    for existing in versions:
        if existing.get("effective_to") in (None, ""):
            previous_from = date.fromisoformat(existing["effective_from"])
            if previous_from >= effective_from:
                raise NormEditError(
                    f"новая редакция начинается {effective_from}, но открытая "
                    f"редакция {existing['version_id']} действует с {previous_from}"
                )
            existing["effective_to"] = (
                date.fromordinal(effective_from.toordinal() - 1)
            ).isoformat()

    versions.append(
        {
            "version_id": version_id,
            "norm_id": norm_id,
            "text": text,
            "sanctions": [],
            "effective_from": effective_from.isoformat(),
            "effective_to": effective_to.isoformat() if effective_to else None,
            "source_url": source_url,
            "source_edition": source_edition,
            "retrieved_at": date.today().isoformat(),
            # Опечатывание — отдельное действие после юридической сверки.
            "sha256": UNSEALED,
            "verification_status": "draft",
        }
    )
    _write(path, payload)
    return path


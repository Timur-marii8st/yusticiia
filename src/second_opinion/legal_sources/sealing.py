"""Опечатывание нормативного корпуса.

Зачем нужен отдельный механизм, если у каждой редакции уже есть
``sha256``.

``NormVersion.sha256`` — контрольная сумма **текста цитаты**. Она едет
вместе с отчётом и позволяет позже убедиться, что показанная судье цитата
не подменена. Для этого она и нужна, и такой её и оставляем.

Целостность **файла фикстуры** она не обеспечивает, по двум причинам:

1. Валидатор ``NormVersion`` требует ``sha256 == sha256(text)``, поэтому
   редакция с несходящейся суммой просто не создаётся. Любая проверка
   «сумма сходится» над уже загруженной моделью — тавтология.
2. Сумма считается только от текста. ``sanctions`` — пределы наказания,
   которые движок правил сравнивает со сроком из проекта акта, — в неё не
   входят вовсе. Правка ``max_months`` с 24 на 120 оставляет и сумму, и
   все проверки зелёными.

Манифест закрывает и то, и другое: он хранится на диске рядом с
фикстурами и фиксирует хэш **всей записи редакции** — текст, даты,
санкции, статус сверки, источник. Добавление и удаление редакции или
нормы он тоже видит, чего пер-версионные суммы не умеют в принципе.

Опечатывание — осознанное действие оператора (``second-opinion
norms-seal``), а не побочный эффект загрузки. Иначе правка снова
«подтверждала» бы саму себя.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

MANIFEST_NAME = "MANIFEST.json"

#: Значение-заглушка в неопечатанных фикстурах.
UNSEALED = "AUTOFILL"


class UnsealedNormError(ValueError):
    """Фикстура не опечатана: контрольная сумма редакции не заполнена."""


def _canonical(payload: dict) -> str:
    """Каноническая форма записи редакции: сортированный компактный JSON.

    Из хэшируемого исключается сама ``sha256`` — иначе запись зависела бы
    от собственного хэша.
    """
    body = {k: v for k, v in payload.items() if k != "sha256"}
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def version_digest(payload: dict) -> str:
    """Хэш всей записи редакции, а не только её текста."""
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def text_digest(text: str) -> str:
    """Контрольная сумма текста — та, что едет в отчёт."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class SealReport:
    """Расхождения между фикстурами и манифестом."""

    checked: int = 0
    unsealed: list[str] = field(default_factory=list)
    text_mismatch: list[str] = field(default_factory=list)
    record_mismatch: list[str] = field(default_factory=list)
    missing_from_manifest: list[str] = field(default_factory=list)
    missing_from_corpus: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (
            self.unsealed
            or self.text_mismatch
            or self.record_mismatch
            or self.missing_from_manifest
            or self.missing_from_corpus
        )

    def describe(self) -> str:
        if self.ok:
            return f"Корпус опечатан: {self.checked} редакций, расхождений нет."
        lines = [f"Проверено редакций: {self.checked}", ""]
        blocks = [
            ("Не опечатаны (sha256 не заполнена)", self.unsealed),
            ("Текст разошёлся с контрольной суммой", self.text_mismatch),
            ("Запись изменена после опечатывания", self.record_mismatch),
            ("Нет в манифесте (добавлены без опечатывания)", self.missing_from_manifest),
            ("Есть в манифесте, но пропали из корпуса", self.missing_from_corpus),
        ]
        for title, items in blocks:
            if items:
                lines.append(f"{title}: {len(items)}")
                lines += [f"  {item}" for item in sorted(items)]
                lines.append("")
        return "\n".join(lines).rstrip()


def _iter_versions(directory: Path):
    """Пройти по редакциям корпуса: (файл, norm_id, version_id, запись)."""
    for path in sorted(Path(directory).glob("*.json")):
        if path.name == MANIFEST_NAME:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for norm in payload.get("norms", []):
            for version in norm.get("versions", []):
                yield path, norm["norm_id"], version["version_id"], version


def read_manifest(directory: Path) -> dict:
    path = Path(directory) / MANIFEST_NAME
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def check(directory: Path) -> SealReport:
    """Сверить фикстуры с манифестом, ничего не записывая."""
    manifest = read_manifest(directory)
    recorded: dict[str, str] = manifest.get("versions", {})
    report = SealReport()
    seen: set[str] = set()

    for _path, norm_id, version_id, version in _iter_versions(directory):
        key = f"{norm_id}@{version_id}"
        seen.add(key)
        report.checked += 1

        stored_sha = version.get("sha256", "")
        if not stored_sha or stored_sha == UNSEALED:
            report.unsealed.append(key)
            continue
        if stored_sha != text_digest(version["text"]):
            report.text_mismatch.append(key)

        expected = recorded.get(key)
        if expected is None:
            report.missing_from_manifest.append(key)
        elif expected != version_digest(version):
            report.record_mismatch.append(key)

    report.missing_from_corpus.extend(set(recorded) - seen)
    return report


def seal(directory: Path, *, sealed_at: date | None = None) -> SealReport:
    """Записать контрольные суммы в фикстуры и пересобрать манифест.

    Действие осознанное: вызывается после юридической сверки, а не при
    каждой загрузке. До вызова расхождения видны через ``check``.
    """
    directory = Path(directory)
    versions: dict[str, str] = {}

    for path in sorted(directory.glob("*.json")):
        if path.name == MANIFEST_NAME:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for norm in payload.get("norms", []):
            for version in norm.get("versions", []):
                version["sha256"] = text_digest(version["text"])
                versions[f"{norm['norm_id']}@{version['version_id']}"] = version_digest(
                    version
                )
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    manifest = {
        "sealed_at": (sealed_at or date.today()).isoformat(),
        "versions": dict(sorted(versions.items())),
        "corpus_digest": hashlib.sha256(
            json.dumps(dict(sorted(versions.items())), sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }
    (directory / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return check(directory)

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ..domain.norms import LegalNorm, NormVersion
from .sealing import MANIFEST_NAME, UNSEALED, UnsealedNormError


class NormNotFoundError(KeyError):
    pass


class NoApplicableVersionError(LookupError):
    pass


class NormStore:
    """Хранилище норм с временны́ми редакциями.

    ``get_norm(norm_id, applicable_at)`` возвращает редакцию, действовавшую
    на указанную дату (см. docs/ADR/ADR-002).
    """

    def __init__(self) -> None:
        self._norms: dict[str, LegalNorm] = {}
        self._versions: dict[str, list[NormVersion]] = {}

    # -- загрузка

    @classmethod
    def from_directory(cls, directory: Path) -> NormStore:
        store = cls()
        for path in sorted(Path(directory).glob("*.json")):
            if path.name == MANIFEST_NAME:
                continue
            store.load_file(path)
        return store

    def load_file(self, path: Path) -> None:
        with Path(path).open(encoding="utf-8") as fh:
            payload = json.load(fh)
        code = payload.get("code", "")
        source_document = payload.get("source_document", "")
        for norm_payload in payload.get("norms", []):
            norm = LegalNorm(
                norm_id=norm_payload["norm_id"],
                code=norm_payload.get("code", code),
                article=norm_payload["article"],
                part=norm_payload.get("part"),
                article_suffix=norm_payload.get("article_suffix"),
                title=norm_payload.get("title", ""),
                amendments_checked_through=norm_payload.get("amendments_checked_through"),
                amendments_checked_source=norm_payload.get(
                    "amendments_checked_source", ""
                ),
            )
            versions = []
            for version in norm_payload["versions"]:
                version = dict(version)
                sha256 = version.get("sha256", "")
                if not sha256 or sha256 == UNSEALED:
                    # Сумма не подставляется на лету: тогда любая правка
                    # текста подтверждала бы саму себя, и контроль целостности
                    # стал бы тавтологией.
                    raise UnsealedNormError(
                        f"норма {norm.norm_id} версия {version['version_id']}: "
                        "контрольная сумма не заполнена — опечатайте корпус "
                        "командой `second-opinion norms-seal`"
                    )
                # Источник берётся из самой редакции, а при отсутствии — из
                # шапки файла: ``setdefault``, а не отдельный аргумент, иначе
                # редакция со своим ``source_document`` дублирует ключ.
                version.setdefault("source_document", source_document)
                versions.append(NormVersion(**version))
            self.add_norm(norm, versions)

    def add_norm(self, norm: LegalNorm, versions: list[NormVersion]) -> None:
        if not versions:
            raise ValueError(f"норма {norm.norm_id}: нет ни одной редакции")
        for version in versions:
            if version.norm_id != norm.norm_id:
                raise ValueError(
                    f"норма {norm.norm_id}: версия {version.version_id} "
                    "ссылается на другую норму"
                )
        _validate_unique_version_ids(norm.norm_id, versions)
        _validate_no_overlap(norm.norm_id, versions)
        self._norms[norm.norm_id] = norm
        self._versions[norm.norm_id] = sorted(
            versions, key=lambda v: v.effective_from
        )

    # -- запросы

    def get_norm_meta(self, norm_id: str) -> LegalNorm:
        try:
            return self._norms[norm_id]
        except KeyError:
            raise NormNotFoundError(f"норма не найдена: {norm_id}") from None

    def versions(self, norm_id: str) -> list[NormVersion]:
        self.get_norm_meta(norm_id)
        return list(self._versions[norm_id])

    def get_norm(self, norm_id: str, applicable_at: date) -> NormVersion:
        """Вернуть редакцию нормы, действовавшую на дату ``applicable_at``."""
        self.get_norm_meta(norm_id)
        for version in self._versions[norm_id]:
            end = version.effective_to
            if version.effective_from <= applicable_at and (
                end is None or applicable_at <= end
            ):
                return version
        raise NoApplicableVersionError(
            f"норма {norm_id}: нет редакции, действовавшей на {applicable_at.isoformat()}"
        )

    def find_by_article(
        self, code: str, article: int, part: int | None
    ) -> LegalNorm | None:
        for norm in self._norms.values():
            if norm.code == code and norm.article == article and norm.part == part:
                return norm
        return None

    def list_norms(self) -> list[LegalNorm]:
        return sorted(self._norms.values(), key=lambda n: (n.code, n.article, n.part or 0))


def _validate_unique_version_ids(norm_id: str, versions: list[NormVersion]) -> None:
    """Редакции одной нормы обязаны различаться идентификаторами.

    Дубликат означает, что одна редакция подменяет другую при любой
    выборке по version_id — в том числе в ссылках отчёта на норму.
    """
    seen: set[str] = set()
    for version in versions:
        if version.version_id in seen:
            raise ValueError(
                f"норма {norm_id}: идентификатор редакции "
                f"{version.version_id} повторяется"
            )
        seen.add(version.version_id)


def _validate_no_overlap(norm_id: str, versions: list[NormVersion]) -> None:
    ordered = sorted(versions, key=lambda v: v.effective_from)
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        if earlier.effective_to is None or earlier.effective_to >= later.effective_from:
            raise ValueError(
                f"норма {norm_id}: интервалы редакций "
                f"{earlier.version_id} и {later.version_id} пересекаются"
            )

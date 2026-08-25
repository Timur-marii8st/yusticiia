from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ..domain.norms import LegalNorm, NormVersion


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

    # -- загрузка ---------------------------------------------------------

    @classmethod
    def from_directory(cls, directory: Path) -> NormStore:
        store = cls()
        for path in sorted(Path(directory).glob("*.json")):
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
                title=norm_payload.get("title", ""),
            )
            versions = []
            for version in norm_payload["versions"]:
                version = dict(version)
                sha256 = version.get("sha256", "")
                if not sha256 or sha256 == "AUTOFILL":
                    version["sha256"] = NormVersion.compute_sha256(version["text"])
                versions.append(
                    NormVersion(
                        source_document=version.get("source_document", source_document),
                        **version,
                    )
                )
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
        _validate_no_overlap(norm.norm_id, versions)
        self._norms[norm.norm_id] = norm
        self._versions[norm.norm_id] = sorted(
            versions, key=lambda v: v.effective_from
        )

    # -- запросы ----------------------------------------------------------

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


def _validate_no_overlap(norm_id: str, versions: list[NormVersion]) -> None:
    ordered = sorted(versions, key=lambda v: v.effective_from)
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        if earlier.effective_to is None or earlier.effective_to >= later.effective_from:
            raise ValueError(
                f"норма {norm_id}: интервалы редакций "
                f"{earlier.version_id} и {later.version_id} пересекаются"
            )

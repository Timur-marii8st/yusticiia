"""Нормы в PostgreSQL: обновление без выпуска релиза.

Файловый корпус (`NormStore`) требует на каждое изменение нормы коммита и
перезапуска сервиса — юрист зависит от разработчика. Здесь тот же приём,
что и для документов и отчётов: интерфейс один, носитель
выбирается конфигурацией (``SO_NORMS_BACKEND``).

Публичный API совпадает с ``NormStore``, поэтому конвейер не отличает
один носитель от другого.

Чего перенос НЕ решает и что придётся закрывать отдельно:

* ``sanctions`` — пределы наказания, которые читает движок правил, из
  текста не выводятся; их по-прежнему вносит юрист;
* целостность. Файловый корпус защищён манифестом и ревью в git. В базе
  этой защиты нет: гарантию «норму не подменили» дают аудит-журнал с
  актором и права доступа, а не контрольная сумма файла.
"""

from __future__ import annotations

import json
import threading
from datetime import date

from ..domain.norms import LegalNorm, NormVersion
from ..storage.sql import safe_sql_identifier
from .store import NoApplicableVersionError, NormNotFoundError, NormStore


class PostgresNormStore:
    """Нормы и их редакции в PostgreSQL (JSONB)."""

    def __init__(self, dsn: str, *, table_prefix: str = "") -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "norms_backend=postgres требует драйвер: "
                "pip install -e '.[pg]'"
            ) from exc

        self._psycopg = psycopg
        self._dsn = dsn
        prefix = safe_sql_identifier(table_prefix) if table_prefix else ""
        self._norms_table = f"{prefix}legal_norms" if prefix else "legal_norms"
        self._versions_table = (
            f"{prefix}legal_norm_versions" if prefix else "legal_norm_versions"
        )
        self._lock = threading.Lock()
        self._ensure_tables()

    def _connect(self):
        return self._psycopg.connect(self._dsn)

    def _ensure_tables(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._norms_table} ("  # noqa: S608
                "norm_id text PRIMARY KEY, data jsonb NOT NULL)"
            )
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._versions_table} ("  # noqa: S608
                "norm_id text NOT NULL, version_id text NOT NULL, "
                "effective_from date NOT NULL, effective_to date, "
                "data jsonb NOT NULL, "
                "PRIMARY KEY (norm_id, version_id))"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {self._versions_table}_period_idx "
                f"ON {self._versions_table} (norm_id, effective_from)"
            )

    def add_norm(self, norm: LegalNorm, versions: list[NormVersion]) -> None:
        """Сохранить норму с редакциями, проверив инварианты."""
        if not versions:
            raise ValueError(f"норма {norm.norm_id}: нет ни одной редакции")
        probe = NormStore()
        probe.add_norm(norm, versions)

        with self._lock, self._connect() as conn:
            conn.execute(
                f"INSERT INTO {self._norms_table} (norm_id, data) VALUES (%s, %s) "  # noqa: S608
                "ON CONFLICT (norm_id) DO UPDATE SET data = EXCLUDED.data",
                (norm.norm_id, norm.model_dump_json()),
            )
            for version in versions:
                conn.execute(
                    f"INSERT INTO {self._versions_table} "  # noqa: S608
                    "(norm_id, version_id, effective_from, effective_to, data) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (norm_id, version_id) DO UPDATE SET "
                    "effective_from = EXCLUDED.effective_from, "
                    "effective_to = EXCLUDED.effective_to, data = EXCLUDED.data",
                    (
                        norm.norm_id,
                        version.version_id,
                        version.effective_from,
                        version.effective_to,
                        version.model_dump_json(),
                    ),
                )

    def sync_from(self, source: NormStore) -> int:
        """Первичный импорт файлового корпуса в базу."""
        count = 0
        for norm in source.list_norms():
            self.add_norm(norm, source.versions(norm.norm_id))
            count += 1
        return count

    @staticmethod
    def _parse(data, model):
        if isinstance(data, str):
            data = json.loads(data)
        return model.model_validate(data)

    def get_norm_meta(self, norm_id: str) -> LegalNorm:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT data FROM {self._norms_table} WHERE norm_id = %s",  # noqa: S608
                (norm_id,),
            ).fetchone()
        if row is None:
            raise NormNotFoundError(f"норма не найдена: {norm_id}")
        return self._parse(row[0], LegalNorm)

    def versions(self, norm_id: str) -> list[NormVersion]:
        self.get_norm_meta(norm_id)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT data FROM {self._versions_table} "  # noqa: S608
                "WHERE norm_id = %s ORDER BY effective_from",
                (norm_id,),
            ).fetchall()
        return [self._parse(data, NormVersion) for (data,) in rows]

    def get_norm(self, norm_id: str, applicable_at: date) -> NormVersion:
        """Редакция, действовавшая на дату. Отбор делает сама база."""
        self.get_norm_meta(norm_id)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT data FROM {self._versions_table} WHERE norm_id = %s "  # noqa: S608
                "AND effective_from <= %s AND (effective_to IS NULL OR effective_to >= %s) "
                "ORDER BY effective_from DESC LIMIT 1",
                (norm_id, applicable_at, applicable_at),
            ).fetchone()
        if row is None:
            raise NoApplicableVersionError(
                f"норма {norm_id}: нет редакции, действовавшей на {applicable_at.isoformat()}"
            )
        return self._parse(row[0], NormVersion)

    def list_norms(self) -> list[LegalNorm]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT data FROM {self._norms_table} ORDER BY norm_id"  # noqa: S608
            ).fetchall()
        norms = [self._parse(data, LegalNorm) for (data,) in rows]
        return sorted(norms, key=lambda n: (n.code, n.article, n.part or 0))

    def find_by_article(
        self,
        code: str,
        article: int,
        part: int | None,
        article_suffix: str | None = None,
    ) -> LegalNorm | None:
        for norm in self.list_norms():
            if (
                norm.code == code
                and norm.article == article
                and norm.part == part
                and norm.article_suffix == article_suffix
            ):
                return norm
        return None

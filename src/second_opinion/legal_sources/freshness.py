"""Обнаружение устаревших норм: сверка записанной редакции с живым источником.

В ``NormVersion.source_edition`` записано, какой редакции источника
соответствует наш текст (``pravo:102041891:365``). Источник спрашивается,
какая редакция у него последняя. Различие — повод сверить текст, а не
подтверждение изменения конкретной статьи: могли измениться другие
статьи того же документа.
Даты выгрузки (``retrieved_at``) для этого мало: она
говорит, когда текст скачали, а не совпадает ли он с действующим.

Проверяются только **действующие** редакции (``effective_to is None``):
для закрытого интервала вопрос свежести смысла не имеет — он описывает
прошлое, которое не меняется.

Неподготовленная новая редакция или неизвестное место редакции в истории
не позволяют подтвердить актуальность. Старые неподготовленные редакции,
предшествующие последней подготовленной по номеру источника, не означают
нового изменения. Даты актов для определения порядка не используются.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from pydantic import BaseModel

from ..domain.norms import LegalNorm, NormVersion
from .pravo_gov import Edition, prepared_editions

SEPARATOR = ":"

CURRENT = "актуальна"
OUTDATED = "устарела"
UNRECORDED = "редакция источника не записана"
FOREIGN = "другой источник"
UNKNOWN = "источник не знает такой редакции"
UNCERTAIN = "актуальность не подтверждена"


class SourceEdition(BaseModel):
    """Координаты редакции источника: откуда, какой документ, какая редакция."""

    source: str
    document: str
    edition: str

    def __str__(self) -> str:
        return SEPARATOR.join((self.source, self.document, self.edition))


def parse_source_edition(value: str) -> SourceEdition | None:
    """Разобрать ``источник:документ:редакция``.

    Возвращает ``None`` на пустой или неразборчивой строке: пропуск здесь
    честнее исключения — норма просто остаётся непроверенной, и это видно
    в отчёте.
    """
    parts = (value or "").split(SEPARATOR)
    if len(parts) != 3 or not all(part.strip() for part in parts):
        return None
    source, document, edition = (part.strip() for part in parts)
    return SourceEdition(source=source, document=document, edition=edition)


def edition_stamp(
    source: str,
    document: str,
    number: int,
    *,
    checked_through: date,
    act_date: date | None,
) -> str:
    """Собрать ``source_edition`` для редакции, которую покрыла сверка.

    Отметка означает утверждение «наш текст соответствует этой редакции
    источника». Оно обосновано ровно тогда, когда юрист сверял текст уже
    после того, как изменяющий акт был подписан. Сверка от 2024 года не
    может подтверждать редакцию, введённую законом 2025 года, — такая
    отметка выглядела бы как проверенный факт, оставаясь допущением.
    """
    if act_date is not None and checked_through < act_date:
        raise ValueError(
            f"сверка от {checked_through.isoformat()} не покрывает редакцию "
            f"{number}: изменяющий акт подписан {act_date.isoformat()}. "
            "Сверьте текст по действующей редакции и укажите её дату"
        )
    return str(SourceEdition(source=source, document=document, edition=str(number)))


class NormFreshness(BaseModel):
    """Состояние одной действующей редакции относительно источника."""

    norm_id: str
    ref: str
    version_id: str
    status: str
    recorded: str = ""
    latest: str = ""
    note: str = ""


class FreshnessReport(BaseModel):
    """Итог проверки корпуса на обновления."""

    source: str
    checked: int = 0
    results: list[NormFreshness] = []

    def by_status(self, status: str) -> list[NormFreshness]:
        return [r for r in self.results if r.status == status]

    @property
    def outdated(self) -> list[NormFreshness]:
        return self.by_status(OUTDATED)

    @property
    def uncertain(self) -> list[NormFreshness]:
        return self.by_status(UNCERTAIN)

    @property
    def unverifiable(self) -> list[NormFreshness]:
        """Нормы, о свежести которых сказать нечего."""
        return [
            r for r in self.results
            if r.status in (UNRECORDED, FOREIGN, UNKNOWN, UNCERTAIN)
        ]

    @property
    def ok(self) -> bool:
        return not (self.outdated or self.uncertain)


def _current_versions(versions: Iterable[NormVersion]) -> list[NormVersion]:
    return [v for v in versions if v.effective_to is None]


def _pending_editions(editions: list[Edition], latest_number: int) -> list[Edition]:
    """Редакции, которые нельзя считать покрытыми подготовленным текстом.

    Номер в подписи ИПС позволяет отличить старые пробелы от новых даже
    при неизвестных или немонотонных датах актов. Без номера порядок
    неизвестен: дата сама по себе не доказывает, что акт уже учтён.
    """
    return [
        edition for edition in editions
        if not edition.prepared
        and (edition.listed_number is None or edition.listed_number >= latest_number)
    ]


def check_updates(
    source, items: Iterable[tuple[LegalNorm, list[NormVersion]]]
) -> FreshnessReport:
    """Сверить действующие редакции корпуса с последними у источника.

    Список редакций запрашивается по одному разу на документ: все статьи
    одного кодекса живут в одном документе источника, и опрашивать его на
    каждую статью значило бы бить по чужому серверу без нужды.
    """
    report = FreshnessReport(source=getattr(source, "name", "?"))
    latest_by_document: dict[str, tuple[str | None, list[Edition]]] = {}

    for norm, versions in items:
        for version in _current_versions(versions):
            report.checked += 1
            recorded = parse_source_edition(version.source_edition)

            if recorded is None:
                report.results.append(
                    NormFreshness(
                        norm_id=norm.norm_id,
                        ref=norm.ref,
                        version_id=version.version_id,
                        status=UNRECORDED,
                        note=(
                            "в редакции не записано, какой редакции источника "
                            "соответствует текст"
                        ),
                    )
                )
                continue

            if recorded.source != report.source:
                report.results.append(
                    NormFreshness(
                        norm_id=norm.norm_id,
                        ref=norm.ref,
                        version_id=version.version_id,
                        status=FOREIGN,
                        recorded=str(recorded),
                        note=f"текст взят из источника {recorded.source!r}",
                    )
                )
                continue

            if recorded.document not in latest_by_document:
                editions = source.editions(recorded.document)
                prepared = prepared_editions(editions)
                latest_number = prepared[-1].number if prepared else None
                latest_by_document[recorded.document] = (
                    str(latest_number) if latest_number is not None else None,
                    _pending_editions(editions, latest_number)
                    if latest_number is not None else list(editions),
                )
            latest, pending = latest_by_document[recorded.document]
            note = ""
            if pending:
                labels = "; ".join(e.label or "редакция без подписи" for e in pending)
                note = (
                    "консолидированный текст не подготовлен для новых редакций "
                    "или редакций с неизвестным порядком: "
                    f"{labels}. Актуальность не подтверждена; затронута ли "
                    "эта статья, требуется сверить по изменяющим актам"
                )

            if latest is None:
                report.results.append(
                    NormFreshness(
                        norm_id=norm.norm_id,
                        ref=norm.ref,
                        version_id=version.version_id,
                        status=UNCERTAIN if pending else UNKNOWN,
                        recorded=str(recorded),
                        note=note or "у источника нет ни одной подготовленной редакции",
                    )
                )
                continue

            status = CURRENT if recorded.edition == latest else OUTDATED
            if pending and status == CURRENT:
                status = UNCERTAIN
            report.results.append(
                NormFreshness(
                    norm_id=norm.norm_id,
                    ref=norm.ref,
                    version_id=version.version_id,
                    status=status,
                    recorded=str(recorded),
                    latest=latest,
                    note=note,
                )
            )

    return report

"""Обнаружение устаревших норм: сверка записанной редакции с живым источником.

Раньше «актуальность» держалась на том, что кто-то однажды скачал текст:
`retrieved_at` сохранялся, но ни с чем не сравнивался, и норма, взятая в
2020 году, выглядела так же надёжно, как взятая вчера. Закон мог
измениться назавтра после выгрузки — система об этом не узнавала.

Здесь сравнение по существу. В `NormVersion.source_edition` записано,
какой редакции источника соответствует наш текст
(``pravo:102041891:365``). Источник спрашивается, какая редакция у него
последняя. Различие означает, что норму изменили после выгрузки.

Проверяются только **действующие** редакции (``effective_to is None``):
для закрытого интервала вопрос свежести смысла не имеет — он описывает
прошлое, которое не меняется.

Чего здесь нет: детектор говорит «норму изменили», а не «что именно
изменилось». Разбор изменения — работа юриста, и `norms-check-amendments`
показывает ему, какие статьи затронуты.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel

from ..domain.norms import LegalNorm, NormVersion
from .pravo_gov import prepared_editions

#: Разделитель координат в ``source_edition``.
SEPARATOR = ":"

#: Норма соответствует последней редакции источника.
CURRENT = "актуальна"
#: Источник знает более новую редакцию — норму изменили после выгрузки.
OUTDATED = "устарела"
#: ``source_edition`` пуст: сравнивать не с чем.
UNRECORDED = "редакция источника не записана"
#: Текст взят из другого источника, чем опрашиваемый.
FOREIGN = "другой источник"
#: Записанной редакции в списке источника нет.
UNKNOWN = "источник не знает такой редакции"


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
    def unverifiable(self) -> list[NormFreshness]:
        """Нормы, о свежести которых сказать нечего."""
        return [r for r in self.results if r.status in (UNRECORDED, FOREIGN, UNKNOWN)]

    @property
    def ok(self) -> bool:
        return not self.outdated


def _current_versions(versions: Iterable[NormVersion]) -> list[NormVersion]:
    return [v for v in versions if v.effective_to is None]


def check_updates(
    source, items: Iterable[tuple[LegalNorm, list[NormVersion]]]
) -> FreshnessReport:
    """Сверить действующие редакции корпуса с последними у источника.

    Список редакций запрашивается по одному разу на документ: все статьи
    одного кодекса живут в одном документе источника, и опрашивать его на
    каждую статью значило бы бить по чужому серверу без нужды.
    """
    report = FreshnessReport(source=getattr(source, "name", "?"))
    latest_by_document: dict[str, str | None] = {}

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
                editions = prepared_editions(source.editions(recorded.document))
                latest_by_document[recorded.document] = (
                    str(editions[-1].number) if editions else None
                )
            latest = latest_by_document[recorded.document]

            if latest is None:
                report.results.append(
                    NormFreshness(
                        norm_id=norm.norm_id,
                        ref=norm.ref,
                        version_id=version.version_id,
                        status=UNKNOWN,
                        recorded=str(recorded),
                        note="у источника нет ни одной подготовленной редакции",
                    )
                )
                continue

            status = CURRENT if recorded.edition == latest else OUTDATED
            report.results.append(
                NormFreshness(
                    norm_id=norm.norm_id,
                    ref=norm.ref,
                    version_id=version.version_id,
                    status=status,
                    recorded=str(recorded),
                    latest=latest,
                )
            )

    return report

"""Обнаружение устаревших норм: сверка записанной редакции с источником."""

from __future__ import annotations

import hashlib
from datetime import date

from second_opinion.domain.norms import LegalNorm, NormVersion
from second_opinion.legal_sources.freshness import (
    CURRENT,
    FOREIGN,
    OUTDATED,
    UNKNOWN,
    UNRECORDED,
    check_updates,
    parse_source_edition,
)
from second_opinion.legal_sources.pravo_gov import Edition


def _norm(norm_id: str = "uk-rf:art-66", article: int = 66) -> LegalNorm:
    return LegalNorm(norm_id=norm_id, code="УК РФ", article=article, title="Тест")


def _version(
    norm_id: str = "uk-rf:art-66",
    version_id: str = "v-current",
    *,
    source_edition: str = "",
    effective_to: date | None = None,
) -> NormVersion:
    text = f"текст {version_id}"
    return NormVersion(
        norm_id=norm_id,
        version_id=version_id,
        text=text,
        effective_from=date(1996, 7, 1),
        effective_to=effective_to,
        source_document="УК РФ",
        source_edition=source_edition,
        retrieved_at=date(2026, 9, 3),
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


class _FakeSource:
    """Источник с заданным списком редакций и счётчиком обращений."""

    name = "pravo"

    def __init__(self, editions: list[Edition]) -> None:
        self._editions = editions
        self.calls: list[str] = []

    def editions(self, document: str) -> list[Edition]:
        self.calls.append(document)
        return self._editions


def _source(last: int = 365) -> _FakeSource:
    return _FakeSource(
        [
            Edition(number=0, label="Исходная редакция"),
            Edition(number=last, act_date=date(2026, 8, 4), label=str(last)),
        ]
    )


def test_parses_source_edition() -> None:
    parsed = parse_source_edition("pravo:102041891:365")

    assert (parsed.source, parsed.document, parsed.edition) == (
        "pravo",
        "102041891",
        "365",
    )
    assert str(parsed) == "pravo:102041891:365"


def test_unparseable_source_edition_is_none() -> None:
    # Пропуск честнее исключения: норма остаётся непроверенной и это видно.
    assert parse_source_edition("") is None
    assert parse_source_edition("102041891") is None
    assert parse_source_edition("pravo::365") is None


def test_matching_edition_is_current() -> None:
    source = _source()
    items = [(_norm(), [_version(source_edition="pravo:102041891:365")])]

    report = check_updates(source, items)

    assert report.results[0].status == CURRENT
    assert report.ok


def test_older_edition_is_outdated() -> None:
    source = _source()
    items = [(_norm(), [_version(source_edition="pravo:102041891:31")])]

    report = check_updates(source, items)

    assert report.results[0].status == OUTDATED
    assert report.results[0].latest == "365"
    assert not report.ok


def test_missing_edition_is_reported_not_assumed_fresh() -> None:
    source = _source()
    items = [(_norm(), [_version()])]

    report = check_updates(source, items)

    assert report.results[0].status == UNRECORDED
    # Нечего сравнивать — но и «актуальна» писать нельзя.
    assert report.ok
    assert report.unverifiable


def test_other_source_is_not_compared() -> None:
    source = _source()
    items = [(_norm(), [_version(source_edition="garant:12345678:7")])]

    report = check_updates(source, items)

    assert report.results[0].status == FOREIGN
    assert source.calls == []


def test_source_without_prepared_editions() -> None:
    source = _FakeSource([Edition(number=None, label="не готова")])
    items = [(_norm(), [_version(source_edition="pravo:102041891:365")])]

    report = check_updates(source, items)

    assert report.results[0].status == UNKNOWN


def test_closed_versions_are_not_checked() -> None:
    # Закрытый интервал описывает прошлое: вопрос свежести к нему неприменим.
    source = _source()
    items = [
        (
            _norm(),
            [
                _version(version_id="v-1996", effective_to=date(2003, 12, 15)),
                _version(version_id="v-current", source_edition="pravo:102041891:365"),
            ],
        )
    ]

    report = check_updates(source, items)

    assert report.checked == 1
    assert [r.version_id for r in report.results] == ["v-current"]


def test_document_is_requested_once_for_many_norms() -> None:
    # Все статьи кодекса живут в одном документе источника: опрашивать его
    # на каждую статью значило бы бить по чужому серверу без нужды.
    source = _source()
    items = [
        (
            _norm(f"uk-rf:art-{n}", n),
            [_version(f"uk-rf:art-{n}", source_edition="pravo:102041891:365")],
        )
        for n in (60, 61, 66)
    ]

    report = check_updates(source, items)

    assert report.checked == 3
    assert source.calls == ["102041891"]


def test_add_version_records_source_edition(tmp_path) -> None:
    import json

    from second_opinion.legal_sources.editing import add_version

    norm = {
        "norms": [
            {
                "norm_id": "uk-rf:art-66",
                "code": "УК РФ",
                "article": 66,
                "title": "Тест",
                "versions": [
                    {
                        "version_id": "v-1996",
                        "norm_id": "uk-rf:art-66",
                        "text": "старый текст",
                        "sanctions": [],
                        "effective_from": "1996-07-01",
                        "effective_to": None,
                        "source_document": "УК РФ",
                        "source_url": "",
                        "retrieved_at": "2026-09-03",
                        "sha256": hashlib.sha256("старый текст".encode()).hexdigest(),
                        "verification_status": "verified",
                    }
                ],
            }
        ]
    }
    path = tmp_path / "uk.json"
    path.write_text(json.dumps(norm, ensure_ascii=False), encoding="utf-8")

    add_version(
        tmp_path,
        "uk-rf:art-66",
        version_id="v-2026",
        text="новый текст",
        effective_from=date(2026, 8, 4),
        source_edition="pravo:102041891:365",
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    added = saved["norms"][0]["versions"][-1]
    assert added["source_edition"] == "pravo:102041891:365"

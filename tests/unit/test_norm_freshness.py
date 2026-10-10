"""Обнаружение устаревших норм: сверка записанной редакции с источником."""

from __future__ import annotations

import hashlib
from datetime import date

import pytest

from second_opinion.domain.norms import LegalNorm, NormVersion
from second_opinion.legal_sources.freshness import (
    CURRENT,
    FOREIGN,
    OUTDATED,
    UNCERTAIN,
    UNKNOWN,
    UNRECORDED,
    check_updates,
    edition_stamp,
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
    assert parse_source_edition("") is None
    assert parse_source_edition("102041891") is None
    assert parse_source_edition("pravo::365") is None


def test_matching_edition_is_current() -> None:
    source = _source()
    items = [(_norm(), [_version(source_edition="pravo:102041891:365")])]

    report = check_updates(source, items)

    assert report.results[0].status == CURRENT
    assert report.ok


@pytest.mark.parametrize(
    "pending",
    [
        Edition(number=None, act_date=date(2026, 10, 9), label="366 - новый акт (не готова)"),
        # Дата акта может быть раньше, хотя номер редакции больше.
        Edition(number=None, act_date=date(2025, 1, 1), label="366 - ранний акт (не готова)"),
        Edition(number=None, label="366 - дата неизвестна (не готова)"),
        Edition(number=None, label="365 - тот же номер, текст не готов"),
        # Если порядок неизвестен, даже старая дата не доказывает полноту текста.
        Edition(number=None, act_date=date(2010, 1, 1), label="не готова"),
        Edition(number=None, label="не готова, дата неизвестна"),
    ],
)
def test_unprepared_new_or_unordered_edition_prevents_current(pending) -> None:
    source = _source()
    source._editions.append(pending)
    items = [(_norm(), [_version(source_edition="pravo:102041891:365")])]

    report = check_updates(source, items)

    assert report.results[0].status == UNCERTAIN
    assert report.results[0].latest == "365"
    assert report.unverifiable == report.uncertain
    assert not report.by_status(CURRENT)
    assert not report.outdated, "неподготовленный текст не доказывает изменение статьи"
    assert not report.ok, "мониторинг должен уведомить о непроверенной актуальности"
    assert pending.label in report.results[0].note


def test_old_unprepared_gap_does_not_hide_verified_current_edition() -> None:
    source = _FakeSource(
        [
            Edition(number=355, act_date=date(2025, 10, 27), label="355"),
            # Старая по номеру редакция имеет более позднюю дату акта.
            Edition(number=None, act_date=date(2025, 11, 18), label="354 - акт (не готова)"),
            Edition(number=353, act_date=date(2025, 11, 17), label="353"),
            Edition(number=None, label="80 - дата неизвестна (не готова)"),
            Edition(number=0, label="Исходная редакция"),
        ]
    )
    items = [(_norm(), [_version(source_edition="pravo:102041891:355")])]

    report = check_updates(source, items)

    assert report.results[0].status == CURRENT
    assert report.results[0].latest == "355"
    assert report.ok
    assert not report.unverifiable


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
    items = [
        (
            _norm(f"uk-rf:art-{article}", article),
            [_version(f"uk-rf:art-{article}", source_edition="pravo:102041891:365")],
        )
        for article in (66, 73)
    ]

    report = check_updates(source, items)

    assert [result.status for result in report.results] == [UNCERTAIN, UNCERTAIN]
    assert not report.ok
    assert all("не подготовлен" in result.note for result in report.results)
    assert source.calls == ["102041891"]


def test_empty_source_result_keeps_unknown_status() -> None:
    items = [(_norm(), [_version(source_edition="pravo:102041891:365")])]

    report = check_updates(_FakeSource([]), items)

    assert report.results[0].status == UNKNOWN
    assert report.ok


def test_closed_versions_are_not_checked() -> None:
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


@pytest.mark.parametrize("recorded, expected_code", [("pravo:102041891:365", 1), ("", 0)])
def test_cli_notifies_about_new_unprepared_editions_without_changing_unrecorded_policy(
    monkeypatch, capsys, recorded, expected_code
) -> None:
    from second_opinion.cli import redactions
    from second_opinion.legal_sources.store import NormStore

    source = _source()
    source._editions.append(Edition(number=None, label="366 - акт (не готова)"))
    store = NormStore()
    store.add_norm(_norm(), [_version(source_edition=recorded)])
    monkeypatch.setattr(redactions, "pravo_source", lambda: (source, None))
    monkeypatch.setattr("second_opinion.api.deps.build_norm_store", lambda _: store)

    assert redactions.norms_check_updates() == expected_code
    output = capsys.readouterr().out
    assert "Актуальны:" not in output
    if recorded:
        assert "Актуальность не подтверждена" in output
        assert "366 - акт (не готова)" in output
        assert "УСТАРЕЛИ" not in output
    else:
        assert "не записано" in output
        assert source.calls == []


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


def test_edition_stamp_requires_verification_after_the_amendment() -> None:
    """Сверка не может подтверждать редакцию, введённую позже неё.

    Иначе отметка «текст соответствует редакции 365» выглядела бы
    проверенным фактом, оставаясь допущением: юрист смотрел текст до того,
    как изменяющий закон был подписан.
    """
    import pytest

    with pytest.raises(ValueError, match="не покрывает редакцию"):
        edition_stamp(
            "pravo",
            "102041891",
            365,
            checked_through=date(2024, 1, 1),
            act_date=date(2025, 10, 27),
        )


def test_edition_stamp_is_built_when_verification_covers_the_amendment() -> None:
    stamp = edition_stamp(
        "pravo",
        "102041891",
        365,
        checked_through=date(2026, 9, 18),
        act_date=date(2025, 10, 27),
    )
    assert stamp == "pravo:102041891:365"


def test_edition_stamp_without_act_date_is_allowed() -> None:
    """Исходная редакция не имеет изменяющего акта — сверять не с чем."""
    assert (
        edition_stamp(
            "pravo", "102041891", 1, checked_through=date(2026, 9, 18), act_date=None
        )
        == "pravo:102041891:1"
    )

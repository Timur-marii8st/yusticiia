"""ИПС pravo.gov.ru: список редакций, тексты статей, сверка изменений.

Разметка в константах — сокращённые выдержки из реальных ответов ИПС,
снятых 14.09.2026 для документа 102041891 (УК РФ). Сеть в тестах не
используется.
"""

from __future__ import annotations

from datetime import date

import pytest

from second_opinion.config import AppConfig
from second_opinion.legal_sources.pravo_gov import (
    Edition,
    PravoGovSource,
    check_amendments,
    extract_articles,
    is_complete,
    parse_editions,
)
from second_opinion.legal_sources.redaction_source import RedactionSourceError
from second_opinion.legal_sources.source_factory import build_redaction_source

EDITIONS_PAGE = (
    "<select name=\"doc_editions\" onchange=\"ChangeSelRDK(this)\">"
    "<option value='0,102041891' >Исходная редакция</option>"
    "<option value='1,102041891'  class='blue'>1 - от 27.05.1998 № 77-ФЗ (изм.)</option>"
    "<option value='31,102041891'  class='blue'>31 - от 08.12.2003 № 162-ФЗ (изм.)</option>"
    "<option value='n'  class='blue'>80 - от 19.05.2010 № 87-ФЗ (изм.)(не готова)</option>"
    "<option value='365,102041891'  selected class='blue'>365 - от 04.08.2026 № 300-ФЗ (изм.)</option>"
    "</select>"
)

DOCUMENT = (
    "<script> var textCompleted = 'True'.toLowerCase() == 'true';</script>"
    "<div id=\"text_content\">"
    "<p>Статья 66. Назначение наказания за неоконченное преступление</p>"
    "<p>1. При назначении наказания за неоконченное преступление учитываются"
    "  обстоятельства, в силу которых преступление не было доведено до конца.</p>"
    "<p>Статья 73. Условное осуждение</p>"
    "<p>1. Если суд придёт к выводу о возможности исправления осужденного.</p>"
    "</div>"
)

TRUNCATED = DOCUMENT.replace("'True'", "'False'")


def _config(tmp_path, **overrides) -> AppConfig:
    base = {
        "data_dir": tmp_path,
        "fixtures_dir": tmp_path / "fixtures",
        "llm_provider": "mock",
        "openai_base_url": "",
        "openai_api_key": "",
        "llm_model": "",
        "max_upload_bytes": 1000,
    }
    base.update(overrides)
    return AppConfig(**base)


class _FakeSource:
    """Источник с заранее заданными редакциями и текстами."""

    def __init__(self, editions, texts) -> None:
        self._editions = editions
        self._texts = texts

    def editions(self, topic: str):
        return self._editions

    def article_texts(self, topic: str, rdk: int):
        return self._texts[rdk]


def test_parses_editions_with_numbers_and_act_dates() -> None:
    editions = parse_editions(EDITIONS_PAGE)

    assert [e.number for e in editions] == [0, 1, 31, None, 365]
    assert editions[1].act_date == date(1998, 5, 27)
    assert editions[1].act_name == "77-ФЗ"


def test_original_edition_has_no_act_date() -> None:
    # Дату вступления исходной редакции в силу страница не сообщает,
    # и подставлять её неоткуда.
    original = parse_editions(EDITIONS_PAGE)[0]

    assert original.act_date is None
    assert original.prepared


def test_unprepared_edition_has_no_number() -> None:
    # «не готова» — текста в ИПС нет, запросить редакцию нельзя.
    unprepared = [e for e in parse_editions(EDITIONS_PAGE) if not e.prepared]

    assert len(unprepared) == 1
    assert unprepared[0].act_date == date(2010, 5, 19)


def test_page_without_editions_is_an_error() -> None:
    with pytest.raises(RedactionSourceError, match="список редакций"):
        parse_editions("<html><body>Документ не найден</body></html>")


def test_extracts_articles_and_normalises_spacing() -> None:
    articles = extract_articles(DOCUMENT)

    assert set(articles) == {"66", "73"}
    assert "  " not in articles["66"]
    assert articles["66"].startswith("Назначение наказания")


def test_completeness_flag_is_read_from_page() -> None:
    assert is_complete(DOCUMENT)
    assert not is_complete(TRUNCATED)


def test_truncated_document_is_refused(monkeypatch) -> None:
    # Обрыв текста выглядит как отсутствие статьи: если его не заметить,
    # сверка объявит «статья не найдена» вместо честного отказа.
    source = PravoGovSource()
    monkeypatch.setattr(source, "_get", lambda params: TRUNCATED)

    with pytest.raises(RedactionSourceError, match="неполный текст"):
        source.article_texts("102041891", 365)


def test_topic_must_be_numeric() -> None:
    with pytest.raises(RedactionSourceError, match="102041891"):
        PravoGovSource().editions("ук-рф")


def test_base_url_must_carry_protocol() -> None:
    with pytest.raises(ValueError, match="http://"):
        PravoGovSource(base_url="pravo.gov.ru")


def test_redactions_keep_unprepared_as_uncertain(monkeypatch) -> None:
    source = PravoGovSource()
    monkeypatch.setattr(source, "_get", lambda params: EDITIONS_PAGE)

    history = source.redactions("102041891")

    assert [r.valid_from.year for r in history.redactions] == [1998, 2003, 2026]
    # Исходная редакция и неподготовленная — обе в неопределённости.
    assert len(history.uncertain) == 2
    assert any("не подготовлен" in p.description for p in history.uncertain)


def test_redactions_state_that_dates_are_act_dates(monkeypatch) -> None:
    # Дата акта не равна дате вступления в силу, и это должно быть видно
    # тому, кто возьмёт границы редакций в фикстуру.
    source = PravoGovSource()
    monkeypatch.setattr(source, "_get", lambda params: EDITIONS_PAGE)

    history = source.redactions("102041891")

    assert all("дата акта" in r.status for r in history.redactions)


def test_check_reports_changed_and_unchanged_articles() -> None:
    fake = _FakeSource(
        editions=[
            Edition(number=0, label="Исходная редакция"),
            Edition(number=365, act_date=date(2026, 8, 4), label="365"),
        ],
        texts={
            0: {"66": "прежний текст", "73": "прежний текст"},
            365: {"66": "прежний текст", "73": "новый текст"},
        },
    )

    report = check_amendments(fake, "102041891", ["66", "73"])

    assert {v.article: v.changed for v in report.verdicts} == {"66": False, "73": True}


def test_check_does_not_guess_when_article_is_missing() -> None:
    fake = _FakeSource(
        editions=[
            Edition(number=0, label="Исходная редакция"),
            Edition(number=365, act_date=date(2026, 8, 4), label="365"),
        ],
        texts={0: {"66": "текст"}, 365: {}},
    )

    report = check_amendments(fake, "102041891", ["66"])

    assert report.verdicts[0].changed is None
    assert "последней" in report.verdicts[0].note


def test_check_needs_two_prepared_editions() -> None:
    fake = _FakeSource(
        editions=[Edition(number=None, act_date=date(2010, 5, 19), label="не готова")],
        texts={},
    )

    with pytest.raises(RedactionSourceError, match="меньше двух"):
        check_amendments(fake, "102041891", ["66"])


def test_factory_builds_pravo_source(tmp_path) -> None:
    source = build_redaction_source(_config(tmp_path, redaction_source="pravo"))

    assert isinstance(source, PravoGovSource)
    assert source.name == "pravo"


def test_disabled_source_names_pravo_as_an_option(tmp_path) -> None:
    with pytest.raises(RedactionSourceError, match="pravo"):
        build_redaction_source(_config(tmp_path))

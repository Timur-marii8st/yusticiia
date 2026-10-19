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
    EditionListMissing,
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
    original = parse_editions(EDITIONS_PAGE)[0]

    assert original.act_date is None
    assert original.prepared


def test_unprepared_edition_has_no_number() -> None:
    unprepared = [e for e in parse_editions(EDITIONS_PAGE) if not e.prepared]

    assert len(unprepared) == 1
    assert unprepared[0].act_date == date(2010, 5, 19)
    assert unprepared[0].listed_number == 80
    assert unprepared[0].number is None, "номер из подписи нельзя использовать как доступный rdk"


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
    assert len(history.uncertain) == 2
    assert any("не подготовлен" in p.description for p in history.uncertain)


def test_redactions_state_that_dates_are_act_dates(monkeypatch) -> None:
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


def test_timeout_is_reported_as_slowness_not_absence(monkeypatch) -> None:
    """«Сервер медленный» и «сервера нет» лечатся по-разному.

    ИПС отвечает 16 с на страницу списка редакций и отдаёт 2,6 МБ полного
    текста кодекса, поэтому таймаут здесь — обычное дело, а не поломка.
    """
    import httpx

    def _timeout(*args, **kwargs):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx, "get", _timeout)
    monkeypatch.setattr("second_opinion.legal_sources.pravo_gov.time.sleep", lambda _: None)

    with pytest.raises(RedactionSourceError, match="SO_PRAVO_TIMEOUT"):
        PravoGovSource(timeout=180.0).editions("102041891")


def _response(status: int, body: bytes = b""):
    import httpx

    return httpx.Response(status, content=body, request=httpx.Request("GET", "http://x"))


def test_empty_answer_is_retried_with_pause(monkeypatch) -> None:
    import httpx

    answers = [_response(204), _response(503), _response(200, "тест".encode("windows-1251"))]
    pauses: list[float] = []
    monkeypatch.setattr(httpx, "get", lambda *a, **k: answers.pop(0))
    monkeypatch.setattr("second_opinion.legal_sources.pravo_gov.time.sleep", pauses.append)

    assert PravoGovSource(backoff=2.0)._get({"nd": "1"}) == "тест"
    assert pauses == [2.0, 4.0]


def test_retries_are_limited_and_failure_is_reported(monkeypatch) -> None:
    import httpx

    calls: list[int] = []

    def _always_empty(*args, **kwargs):
        calls.append(1)
        return _response(204)

    monkeypatch.setattr(httpx, "get", _always_empty)
    monkeypatch.setattr("second_opinion.legal_sources.pravo_gov.time.sleep", lambda _: None)

    with pytest.raises(RedactionSourceError, match="HTTP 204"):
        PravoGovSource(retries=2)._get({"nd": "1"})
    assert len(calls) == 3


def test_not_found_is_not_retried(monkeypatch) -> None:
    import httpx

    calls: list[int] = []

    def _missing(*args, **kwargs):
        calls.append(1)
        return _response(404)

    monkeypatch.setattr(httpx, "get", _missing)

    with pytest.raises(RedactionSourceError, match="HTTP 404"):
        PravoGovSource()._get({"nd": "1"})
    assert len(calls) == 1


def test_factory_passes_timeout_from_config(tmp_path) -> None:
    source = build_redaction_source(
        _config(tmp_path, redaction_source="pravo", pravo_timeout_seconds=42.0)
    )

    assert source._timeout == 42.0


def test_editions_are_ordered_by_number_not_by_date() -> None:
    """Даты актов в выдаче ИПС идут не по порядку.

    В реальном списке редакция 355 датирована 27.10.2025, а 353 —
    17.11.2025: нумерация отражает очерёдность внесения изменений, а не
    даты подписания законов. Сортировка по дате выбрала бы не ту «последнюю»
    редакцию, и сверка сравнивала бы не с действующим текстом.
    """
    from datetime import date as _date

    from second_opinion.legal_sources.pravo_gov import prepared_editions

    editions = [
        Edition(number=353, act_date=_date(2025, 11, 17), label="353"),
        Edition(number=None, act_date=_date(2010, 5, 19), label="не готова"),
        Edition(number=355, act_date=_date(2025, 10, 27), label="355"),
        Edition(number=0, label="Исходная редакция"),
    ]

    ordered = prepared_editions(editions)

    assert [e.number for e in ordered] == [0, 353, 355]


def test_page_without_edition_list_is_retried(monkeypatch) -> None:
    pages = ["<html>сбой</html>", EDITIONS_PAGE]
    pauses: list[float] = []
    source = PravoGovSource(backoff=3.0)
    monkeypatch.setattr(source, "_get", lambda params: pages.pop(0))
    monkeypatch.setattr("second_opinion.legal_sources.pravo_gov.time.sleep", pauses.append)

    assert source.editions("102041891")
    assert pauses == [3.0]


def test_persistent_page_without_edition_list_fails_after_retries(monkeypatch) -> None:
    calls: list[int] = []

    def _broken(params):
        calls.append(1)
        return "<html>сбой</html>"

    source = PravoGovSource(retries=2)
    monkeypatch.setattr(source, "_get", _broken)
    monkeypatch.setattr("second_opinion.legal_sources.pravo_gov.time.sleep", lambda _: None)

    with pytest.raises(EditionListMissing):
        source.editions("102041891")
    assert len(calls) == 3


def test_connection_reset_is_retried(monkeypatch) -> None:
    import httpx

    answers: list[object] = [httpx.ReadError("reset"), _response(200, "тест".encode("windows-1251"))]

    def _get(*args, **kwargs):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    pauses: list[float] = []
    monkeypatch.setattr(httpx, "get", _get)
    monkeypatch.setattr("second_opinion.legal_sources.pravo_gov.time.sleep", pauses.append)

    assert PravoGovSource(backoff=2.0)._get({"nd": "1"}) == "тест"
    assert pauses == [2.0]


def test_persistent_connection_reset_is_reported(monkeypatch) -> None:
    import httpx

    def _reset(*args, **kwargs):
        raise httpx.ReadError("reset")

    monkeypatch.setattr(httpx, "get", _reset)
    monkeypatch.setattr("second_opinion.legal_sources.pravo_gov.time.sleep", lambda _: None)

    with pytest.raises(RedactionSourceError, match="недоступен"):
        PravoGovSource(retries=1)._get({"nd": "1"})


def test_article_with_superscript_number_is_a_separate_article() -> None:
    """ИПС печатает ст. 228.1 как «Статья 228 1 .»; раньше её текст попадал внутрь ст. 228."""
    payload = (
        "<p>Статья 228. Незаконные приобретение наркотических средств</p>"
        "<p>1. Незаконные приобретение — наказывается лишением свободы на срок до трех лет.</p>"
        "<p>Статья 228 1 . Незаконные производство, сбыт</p>"
        "<p>1. Незаконные производство — наказывается лишением свободы на срок от четырех до восьми лет.</p>"
        "<p>Статья 229. Хищение либо вымогательство</p>"
    )
    articles = extract_articles(payload)
    assert set(articles) == {"228", "228.1", "229"}
    assert "производство" not in articles["228"]
    assert articles["228.1"].startswith("Незаконные производство, сбыт")


def test_old_edition_headings_with_indent_and_hyphen_are_articles() -> None:
    """В редакциях до 2019 года ИПС пишет «\\n Статья 161.» и «Статья 228-1.»; раньше такие
    статьи не выделялись, и история показывала мнимые изменения."""
    payload = (
        "<p> Статья 161. Грабеж</p>"
        "<p> 1. Грабеж — наказывается лишением свободы на срок до четырех лет.</p>"
        "<p> Статья 228-1. Незаконные производство, сбыт</p>"
        "<p> 1. Незаконные производство — наказывается лишением свободы.</p>"
        "<p> Статья 229. Хищение</p>"
    )
    articles = extract_articles(payload)
    assert set(articles) == {"161", "228.1", "229"}

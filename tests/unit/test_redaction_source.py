"""Источник редакционной истории: разбор, план редакций, заглушка."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from second_opinion.config import AppConfig
from second_opinion.legal_sources.garant import GarantSource, parse_redactions
from second_opinion.legal_sources.redaction_source import (
    DocumentRedactions,
    Redaction,
    RedactionSourceError,
    plan_versions,
)
from second_opinion.legal_sources.source_factory import build_redaction_source
from second_opinion.legal_sources.stub_source import StubRedactionSource

RESPONSE = [
    {
        "status": "rs_Old",
        "activity": [{"from": "1996-07-01", "to": "2003-12-15"}],
        "changingDocuments": [{"topic": "1", "text": "ФЗ № 63-ФЗ", "entry": "0"}],
        "notSure": [],
    },
    {
        "status": "rs_Actual",
        "activity": [{"from": "2003-12-16", "to": None}],
        "changingDocuments": [{"topic": "2", "text": "ФЗ № 162-ФЗ", "entry": "0"}],
        "notSure": [{"from": "2003-12-16", "to": "2003-12-31", "description": "уточняется"}],
    },
]


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



def test_parses_periods_and_changing_documents() -> None:
    history = parse_redactions("777", RESPONSE)

    assert [r.valid_from for r in history.sorted_redactions()] == [
        date(1996, 7, 1),
        date(2003, 12, 16),
    ]
    assert history.redactions[1].valid_to is None, "открытая редакция"
    assert history.redactions[0].changing_documents[0].text == "ФЗ № 63-ФЗ"


def test_uncertain_periods_are_kept() -> None:
    """Неопределённость скрывать запрещено принципами проекта."""
    history = parse_redactions("777", RESPONSE)
    assert len(history.uncertain) == 1
    assert history.uncertain[0].description == "уточняется"


def test_accepts_wrapped_response() -> None:
    """Документация не даёт однозначного примера верхнего уровня."""
    assert len(parse_redactions("777", {"redactions": RESPONSE}).redactions) == 2


def test_unexpected_payload_type_is_an_error() -> None:
    with pytest.raises(RedactionSourceError, match="неожиданный тип"):
        parse_redactions("777", "строка вместо ответа")


def test_unparseable_date_is_skipped_not_guessed() -> None:
    payload = [{"activity": [{"from": "неизвестно", "to": None}]}]
    assert parse_redactions("777", payload).redactions == []



def test_plan_uses_year_based_ids() -> None:
    planned = plan_versions(parse_redactions("777", RESPONSE))
    assert [p.version_id for p in planned] == ["v-1996", "v-2003"]


def test_plan_disambiguates_same_year() -> None:
    """Повтор идентификатора NormStore отклонит — разводим суффиксом."""
    history = DocumentRedactions(
        topic="777",
        source="test",
        redactions=[
            Redaction(valid_from=date(2011, 3, 1), valid_to=date(2011, 6, 30)),
            Redaction(valid_from=date(2011, 7, 1)),
        ],
    )
    assert [p.version_id for p in plan_versions(history)] == ["v-2011", "v-2011-2"]


def test_plan_from_stub_is_marked() -> None:
    history = parse_redactions("777", RESPONSE).model_copy(update={"live": False})
    assert all("ЗАГЛУШКА" in p.note for p in plan_versions(history))



def _write_stub(directory: Path, topic: str, payload: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{topic}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_stub_marks_data_as_not_live(tmp_path) -> None:
    _write_stub(tmp_path, "777", {"assumed": True, "response": RESPONSE})
    history = StubRedactionSource(tmp_path).redactions("777")

    assert history.live is False
    assert history.source == "stub"
    assert len(history.redactions) == 2


def test_stub_refuses_file_without_assumption_marker(tmp_path) -> None:
    """Предположение по документации не должно сойти за данные сервера."""
    _write_stub(tmp_path, "777", {"response": RESPONSE})

    with pytest.raises(RedactionSourceError, match="assumed"):
        StubRedactionSource(tmp_path).redactions("777")


def test_stub_reports_missing_document(tmp_path) -> None:
    with pytest.raises(RedactionSourceError, match="не содержит документа"):
        StubRedactionSource(tmp_path).redactions("нет-такого")


@pytest.mark.parametrize("topic", ["../secret", "", ".hidden"])
def test_stub_rejects_unsafe_topic(tmp_path, topic: str) -> None:
    with pytest.raises(RedactionSourceError, match="недопустимый идентификатор"):
        StubRedactionSource(tmp_path).redactions(topic)



def test_garant_requires_token_and_base_url() -> None:
    with pytest.raises(ValueError, match="SO_GARANT_BASE_URL"):
        GarantSource("", "токен")
    with pytest.raises(ValueError, match="SO_GARANT_TOKEN"):
        GarantSource("https://api.example.invalid", "")


def test_garant_sends_bearer_and_json_headers() -> None:
    headers = GarantSource("https://api.example.invalid/", "abc").headers
    assert headers["Authorization"] == "Bearer abc"
    assert headers["Accept"] == "application/json"


def test_source_is_off_by_default(tmp_path) -> None:
    """Внешний контур включается только явным решением оператора."""
    with pytest.raises(RedactionSourceError, match="выключен"):
        build_redaction_source(_config(tmp_path))


def test_factory_builds_stub(tmp_path) -> None:
    stub_dir = tmp_path / "stub"
    _write_stub(stub_dir, "777", {"assumed": True, "response": RESPONSE})

    source = build_redaction_source(
        _config(tmp_path, redaction_source="stub", garant_stub_dir=str(stub_dir))
    )
    assert source.name == "stub"


def test_factory_reports_missing_stub_dir(tmp_path) -> None:
    with pytest.raises(RedactionSourceError, match="каталог заглушки"):
        build_redaction_source(
            _config(tmp_path, redaction_source="stub", garant_stub_dir=str(tmp_path / "нет"))
        )


def test_repository_stub_fixture_is_marked() -> None:
    """Фикстура в репозитории обязана объявлять себя предположением."""
    from second_opinion.config import PROJECT_ROOT

    path = PROJECT_ROOT / "data" / "fixtures" / "garant_stub" / "12345678.json"
    assert json.loads(path.read_text(encoding="utf-8"))["assumed"] is True


def test_garant_rejects_base_url_without_protocol() -> None:
    """Частая опечатка: хост без схемы. Сообщать должен проект, а не httpx."""
    with pytest.raises(ValueError, match="http://"):
        GarantSource("api.example.invalid", "токен")


def test_garant_rejects_non_ascii_token() -> None:
    """Кириллица в токене ломала httpx невнятной ошибкой кодировки."""
    with pytest.raises(ValueError, match="не-ASCII"):
        GarantSource("https://api.example.invalid", "ваш-токен")


def test_garant_accepts_documented_token_shape() -> None:
    source = GarantSource("https://api.example.invalid", "U1QtOTkwMTAyLWNud3FpdWhmbzg3M")
    assert source.headers["Authorization"].endswith("U1QtOTkwMTAyLWNud3FpdWhmbzg3M")

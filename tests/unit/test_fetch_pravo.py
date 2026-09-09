from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from fetch_pravo import (  # noqa: E402
    api_document_url,
    api_documents_url,
    build_fixture_payload,
    build_source_document,
    extract_pdf_text,
    load_card,
    main,
)

SAMPLE_PDF = Path(__file__).resolve().parents[1] / "fixtures" / "sample_text.pdf"

BASE = "https://publication.pravo.gov.ru"


def test_api_urls_match_documented_help() -> None:
    assert api_documents_url(BASE) == BASE + "/api/Documents"
    assert (
        api_document_url(BASE, "2600202104190001")
        == BASE + "/api/Document?eoNumber=2600202104190001"
    )


def test_build_source_document_keeps_traceability() -> None:
    card = {
        "name": "Федеральный закон",
        "number": "123-ФЗ",
        "documentDate": "01.01.2024",
        "eoNumber": "2600202104190001",
        "publishDateShort": "02.01.2024",
    }
    doc = build_source_document(card)
    assert "123-ФЗ" in doc
    assert "2600202104190001" in doc


def test_build_fixture_is_draft_with_real_sha(tmp_path: Path) -> None:
    text = "Статья 158 УК РФ. Кража."
    payload = build_fixture_payload(
        norm_id="uk-rf:art-158-part-1-pravo-1",
        code="УК РФ",
        article=158,
        part=1,
        title="Кража",
        text=text,
        effective_from="2024-01-01",
        effective_to=None,
        source_document="ФЗ № 1 (электронное опубликование № 1)",
        source_url=api_document_url(BASE, "1"),
        retrieved_at="2026-09-04",
    )
    version = payload["norms"][0]["versions"][0]
    assert version["verification_status"] == "draft"
    assert version["synthetic"] is False
    assert version["sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()

    # Фикстура обязана грузиться в NormStore без ошибок.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from second_opinion.legal_sources.store import NormStore

    path = tmp_path / "pravo_test.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    store = NormStore()
    store.load_file(path)
    from datetime import date

    loaded = store.get_norm("uk-rf:art-158-part-1-pravo-1", date(2024, 6, 1))
    assert loaded.verification_status.value == "draft"


def test_build_fixture_rejects_empty_text() -> None:
    with pytest.raises(ValueError):
        build_fixture_payload(
            norm_id="x",
            code="УК РФ",
            article=1,
            part=None,
            title="t",
            text="   ",
            effective_from="2024-01-01",
            effective_to=None,
            source_document="doc",
            source_url="url",
            retrieved_at="2026-09-04",
        )


# --- извлечение текста из PDF ------------------------------------------------


def test_extract_pdf_text_reads_text_layer() -> None:
    assert "158" in extract_pdf_text(SAMPLE_PDF)


def test_build_fixture_from_pdf_produces_draft(tmp_path, capsys) -> None:
    """Черновик из PDF: текст извлечён, статус остаётся draft."""
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps({"eoNumber": "0001"}), encoding="utf-8")
    out = tmp_path / "norm.json"

    code = main(
        [
            "build-fixture",
            "--eo-number", "0001",
            "--raw", str(raw),
            "--pdf-file", str(SAMPLE_PDF),
            "--code", "УК РФ",
            "--article", "158",
            "--part", "1",
            "--title", "Кража",
            "--effective-from", "2024-01-01",
            "--out", str(out),
        ]
    )
    assert code == 0

    version = json.loads(out.read_text(encoding="utf-8"))["norms"][0]["versions"][0]
    assert "158" in version["text"]
    assert version["verification_status"].startswith("draft")
    assert "ЧЕРНОВИК" in capsys.readouterr().err


def test_pdf_and_text_file_are_mutually_exclusive(tmp_path) -> None:
    """Два источника текста сразу — ошибка разбора аргументов."""
    with pytest.raises(SystemExit):
        main(
            [
                "build-fixture",
                "--eo-number", "0001",
                "--raw", str(tmp_path / "raw.json"),
                "--pdf-file", str(SAMPLE_PDF),
                "--text-file", str(SAMPLE_PDF),
                "--code", "УК РФ",
                "--article", "158",
                "--title", "Кража",
                "--effective-from", "2024-01-01",
                "--out", str(tmp_path / "n.json"),
            ]
        )


# --- карточка документа ------------------------------------------------------


def test_missing_card_file_is_not_an_error(tmp_path) -> None:
    assert load_card(tmp_path / "нет-такого.json") == {}


def test_empty_card_file_is_not_an_error(tmp_path) -> None:
    """Пустой файл — это отсутствие карточки, а не сбой."""
    empty = tmp_path / "raw.json"
    empty.write_text("", encoding="utf-8")
    assert load_card(empty) == {}


def test_broken_card_file_reports_clearly(tmp_path) -> None:
    """Непустой нечитаемый файл — оборванная загрузка, молчать нельзя."""
    broken = tmp_path / "raw.json"
    broken.write_text("не json", encoding="utf-8")

    with pytest.raises(SystemExit, match="не разбирается как JSON"):
        load_card(broken)


def test_card_wrapper_is_unwrapped(tmp_path) -> None:
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps({"document": {"eoNumber": "0001"}}), encoding="utf-8")
    assert load_card(raw) == {"eoNumber": "0001"}


def test_version_id_follows_effective_year(tmp_path) -> None:
    """Разные редакции нормы не должны получать один идентификатор."""
    raw = tmp_path / "raw.json"
    raw.write_text("{}", encoding="utf-8")

    ids = []
    for effective_from in ("1996-07-01", "2003-12-16"):
        out = tmp_path / f"{effective_from}.json"
        main(
            [
                "build-fixture",
                "--eo-number", "0001",
                "--raw", str(raw),
                "--pdf-file", str(SAMPLE_PDF),
                "--code", "УК РФ",
                "--article", "158",
                "--title", "Кража",
                "--effective-from", effective_from,
                "--out", str(out),
            ]
        )
        ids.append(json.loads(out.read_text(encoding="utf-8"))["norms"][0]["versions"][0]["version_id"])

    assert ids == ["v-1996", "v-2003"]

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
)

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

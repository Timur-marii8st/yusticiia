from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from second_opinion.api.routes import create_app
from tests.conftest import SAMPLE_CLEAN


@pytest.fixture()
def client(pipeline):
    return TestClient(create_app(pipeline))


@pytest.fixture()
def analysis_id(client):
    with SAMPLE_CLEAN.open("rb") as fh:
        response = client.post(
            "/api/documents/upload",
            files={"file": (SAMPLE_CLEAN.name, fh, "text/plain")},
        )
    document_id = response.json()["document_id"]
    response = client.post(f"/api/documents/{document_id}/analyze", json={})
    return response.json()["analysis_id"]


def _quote_of(text: str, needle: str) -> str:
    start = text.index(needle)
    return text[start : start + len(needle)]


def test_add_fact_end_to_end(client, analysis_id) -> None:
    """Судья добавляет факт: цитата из документа, пересчёт отчёта."""
    report_before = client.get(f"/api/analyses/{analysis_id}").json()
    doc = client.get(f"/api/documents/{report_before['document_id']}").json()

    target = "состояние здоровья"
    assert target in doc["text"]
    real_quote = _quote_of(doc["text"], target)

    response = client.post(
        f"/api/analyses/{analysis_id}/facts",
        json={
            "type": "health_factor",
            "value": "хроническое заболевание",
            "quote": real_quote,
        },
    )
    assert response.status_code == 200, response.text
    report = response.json()
    added = [f for f in report["facts"] if f["id"].startswith("fact-health_factor-user")]
    assert len(added) == 1
    fact = added[0]
    assert fact["extraction_method"] == "user"
    assert fact["status"] == "VERIFIED"
    evidence = fact["evidence"][0]
    fragment = doc["text"][evidence["start_offset"] : evidence["end_offset"]]
    assert fragment == real_quote


def test_add_fact_rejects_quote_not_in_document(client, analysis_id) -> None:
    response = client.post(
        f"/api/analyses/{analysis_id}/facts",
        json={"type": "guilty_plea", "value": True, "quote": "такой фразы в документе нет"},
    )
    assert response.status_code == 400
    assert "дословно" in response.json()["detail"]


def test_add_fact_rejects_unknown_type(client, analysis_id) -> None:
    response = client.post(
        f"/api/analyses/{analysis_id}/facts",
        json={"type": "mood_of_judge", "value": "спокойное", "quote": "суд"},
    )
    assert response.status_code == 400


def test_add_fact_rejects_wrong_value_shape(client, analysis_id) -> None:
    report_before = client.get(f"/api/analyses/{analysis_id}").json()
    doc = client.get(f"/api/documents/{report_before['document_id']}").json()
    quote = _quote_of(doc["text"], "возраст")
    response = client.post(
        f"/api/analyses/{analysis_id}/facts",
        json={"type": "defendant_age", "value": "тридцать", "quote": quote},
    )
    assert response.status_code == 400
    assert "150" in response.json()["detail"]


def test_added_qualification_changes_evaluations(client, analysis_id) -> None:
    """Новая квалификация реально участвует в проверках правила-движка:
    R-001 ссылается на добавленный судьёй факт."""
    report_before = client.get(f"/api/analyses/{analysis_id}").json()
    doc = client.get(f"/api/documents/{report_before['document_id']}").json()
    quote = _quote_of(doc["text"], "ч. 2 ст. 158 УК")

    response = client.post(
        f"/api/analyses/{analysis_id}/facts",
        json={"type": "qualification", "value": {"article": 158, "part": 2}, "quote": quote},
    )
    assert response.status_code == 200
    report = response.json()

    new_fact_id = next(
        f["id"]
        for f in report["facts"]
        if f["type"] == "qualification" and f["extraction_method"] == "user"
    )
    r001 = next(ev for ev in report["evaluations"] if ev["rule_id"] == "R-001")
    assert new_fact_id in r001["facts_used"]

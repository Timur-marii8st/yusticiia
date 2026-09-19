"""Личный кабинет: владелец записей и история дел (ФТ-37).

Проверяется главное свойство истории — она принадлежит актору. Чужое дело
в списке судьи это не удобство, а утечка: в материалах дела есть данные
подсудимого, потерпевшего и обстоятельства, которые к другому судье
отношения не имеют.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from second_opinion.api.app import create_app


def _analyze(client: TestClient, text: str, headers: dict[str, str] | None = None) -> str:
    created = client.post(
        "/api/v1/documents",
        json={"filename": "case.txt", "text": text},
        headers=headers or {},
    )
    assert created.status_code == 200, created.text
    document_id = created.json()["document_id"]
    report = client.post(
        f"/api/v1/documents/{document_id}/analyze", json={}, headers=headers or {}
    )
    assert report.status_code == 200, report.text
    return report.json()["analysis_id"]


def test_owner_is_recorded_on_document_and_report(pipeline, sample_clean_text) -> None:
    document = pipeline.ingest("case.txt", sample_clean_text.encode("utf-8"), actor="u-1")
    report = pipeline.analyze(document.document_id, actor="u-1")

    assert document.owner == "u-1"
    assert report.owner == "u-1"


def test_history_shows_only_own_cases(pipeline, sample_clean_text) -> None:
    mine = pipeline.ingest("mine.txt", sample_clean_text.encode("utf-8"), actor="judge-1")
    pipeline.analyze(mine.document_id, actor="judge-1")
    theirs = pipeline.ingest("theirs.txt", sample_clean_text.encode("utf-8"), actor="judge-2")
    pipeline.analyze(theirs.document_id, actor="judge-2")

    own = pipeline.history(owner="judge-1")
    assert len(own) == 1
    assert own[0][0].owner == "judge-1"
    assert own[0][1] is not None and own[0][1].filename == "mine.txt"

    everything = pipeline.history(owner=None)
    assert len(everything) == 2


def test_history_endpoint_returns_summary(pipeline, sample_clean_text) -> None:
    client = TestClient(create_app(pipeline))
    analysis_id = _analyze(client, sample_clean_text)

    response = client.get("/api/v1/analyses?limit=10")
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "own"
    assert body["total"] == 1

    item = body["items"][0]
    assert item["analysis_id"] == analysis_id
    assert item["filename"] == "case.txt"
    assert item["facts_total"] > 0
    # Полный отчёт в списке не возвращается: история открывается часто.
    assert "facts" not in item
    assert sum(item["checks"].values()) > 0


def test_history_is_separated_by_actor_through_api(pipeline, sample_clean_text) -> None:
    """Общий секрет и личная учётная запись — разные акторы, разные списки."""
    client = TestClient(create_app(pipeline, auth_token="shared-secret"))
    shared = {"Authorization": "Bearer shared-secret"}
    _analyze(client, sample_clean_text, headers=shared)

    login = client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    assert login.status_code == 200, login.text
    access = login.json()["tokens"]["access_token"]
    personal = {"Authorization": f"Bearer {access}"}

    under_account = client.get("/api/v1/analyses", headers=personal).json()
    assert under_account["total"] == 0, "дело общего доступа не должно попадать в личную историю"

    _analyze(client, sample_clean_text, headers=personal)
    assert client.get("/api/v1/analyses", headers=personal).json()["total"] == 1
    assert client.get("/api/v1/analyses", headers=shared).json()["total"] == 1


def test_only_admin_sees_every_case(pipeline, sample_clean_text) -> None:
    client = TestClient(create_app(pipeline))
    _analyze(client, sample_clean_text)

    login = client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    access = login.json()["tokens"]["access_token"]
    admin = {"Authorization": f"Bearer {access}"}

    full = client.get("/api/v1/analyses?scope=all", headers=admin)
    assert full.status_code == 200
    assert full.json()["scope"] == "all"
    assert full.json()["total"] == 1

    # Без учётной записи расширенная область запрещена, а не молча сужена:
    # молчаливое сужение выглядело бы как «дел нет».
    denied = client.get("/api/v1/analyses?scope=all")
    assert denied.status_code == 403


def test_account_panel_is_wired_in_the_interface(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    html = client.get("/").text
    js = client.get("/static/app.js").text

    assert 'id="account-panel"' in html
    assert 'for="login-email"' in html
    assert 'for="login-password"' in html
    assert 'id="history-list"' in html

    assert '"/auth/login"' in js
    assert '"/auth/me"' in js
    assert "/api/v1/analyses?limit=" in js
    # Личный токен приоритетнее общего: под ним действия попадают в журнал
    # с именем исполнителя.
    assert 'sessionStorage.getItem("so-access") || sessionStorage.getItem("so-token")' in js

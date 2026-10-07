"""Правка корпуса норм через веб-интерфейс: сверка истории, новая редакция.

До этого обе операции существовали только как CLI-команды — судья-юрист
без опыта работы с терминалом не мог их выполнить. Изолированный корпус в
``tmp_path``, а не общая фикстура ``pipeline``: обработчики пишут прямо в
файлы каталога норм, и прогон против ``data/fixtures/norms`` испортил бы
настоящий корпус проекта.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from second_opinion.api.app import create_app
from second_opinion.api.deps import build_pipeline
from second_opinion.config import AppConfig, reset_config_cache
from second_opinion.legal_sources.sealing import UNSEALED, seal


def _corpus(fixtures_dir) -> None:
    norms_dir = fixtures_dir / "norms"
    norms_dir.mkdir(parents=True)
    payload = {
        "code": "УК РФ",
        "source_document": "Уголовный кодекс Российской Федерации",
        "norms": [
            {
                "norm_id": "uk-rf:art-158-part-1",
                "article": 158,
                "part": 1,
                "title": "Кража",
                "versions": [
                    {
                        "norm_id": "uk-rf:art-158-part-1",
                        "version_id": "v-1996",
                        "text": "текст 1996 года",
                        "sanctions": [
                            {"punishment_type": "imprisonment", "max_months": 24}
                        ],
                        "effective_from": "1996-07-01",
                        "effective_to": None,
                        "retrieved_at": "2026-09-01",
                        "sha256": UNSEALED,
                        "verification_status": "verified",
                    }
                ],
            }
        ],
    }
    (norms_dir / "uk.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    seal(norms_dir)


@pytest.fixture()
def edit_setup(tmp_path, monkeypatch):
    fixtures_dir = tmp_path / "fixtures"
    _corpus(fixtures_dir)
    monkeypatch.setenv("SO_FIXTURES_DIR", str(fixtures_dir))
    reset_config_cache()

    config = AppConfig(
        data_dir=tmp_path / "store",
        fixtures_dir=fixtures_dir,
        llm_provider="mock",
        openai_base_url="",
        openai_api_key="",
        llm_model="",
        max_upload_bytes=1_000_000,
    )
    pipeline = build_pipeline(config)
    client = TestClient(create_app(pipeline))

    login = client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    assert login.status_code == 200, login.text
    admin_headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}

    return client, admin_headers, fixtures_dir / "norms"


def _judge_headers(client: TestClient, admin_headers: dict[str, str]) -> dict[str, str]:
    created = client.post(
        "/auth/users",
        json={
            "email": "judge@example.com",
            "full_name": "Судья",
            "password": "judge-password-123",
            "role": "judge",
        },
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    login = client.post(
        "/auth/login", json={"email": "judge@example.com", "password": "judge-password-123"}
    )
    return {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}



def test_admin_confirms_history(edit_setup) -> None:
    client, admin_headers, norms_dir = edit_setup

    response = client.post(
        "/api/v1/norms/uk-rf:art-158-part-1/confirm-history",
        json={"checked_through": "2026-09-28", "source": "КонсультантПлюс"},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["norm_id"] == "uk-rf:art-158-part-1"
    assert body["sealed"] is True

    payload = json.loads((norms_dir / "uk.json").read_text(encoding="utf-8"))
    norm = payload["norms"][0]
    assert norm["amendments_checked_source"] == "КонсультантПлюс"
    assert norm["versions"][0]["sha256"] != UNSEALED, "корпус обязан остаться опечатанным"


def test_confirm_history_writes_an_audit_entry(edit_setup) -> None:
    """Раньше confirm-history/add-version не писали в аудит-журнал вообще —

    правка боевого корпуса могла остаться без следа, кто и когда её сделал.
    """
    from second_opinion.config import load_config

    client, admin_headers, _ = edit_setup

    response = client.post(
        "/api/v1/norms/uk-rf:art-158-part-1/confirm-history",
        json={"checked_through": "2026-09-28", "source": "КонсультантПлюс"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    events = [
        json.loads(line)
        for line in load_config().audit_path.read_text(encoding="utf-8").splitlines()
    ]
    entry = next(e for e in events if e["operation"] == "norm_confirm_history")
    assert entry["input_hash"] == "uk-rf:art-158-part-1"
    assert entry["details"]["source"] == "КонсультантПлюс"
    assert entry["actor"], "actor обязан быть опознан — это не анонимное действие"


def test_confirm_history_is_visible_without_restart(edit_setup) -> None:
    """Живой NormStore обновляется на месте — без перезапуска процесса."""
    client, admin_headers, _ = edit_setup

    before = client.get("/api/v1/norms/coverage").json()
    flagged_before = {n["norm_id"] for n in before["norms"] if n["needs_review"]}
    assert "uk-rf:art-158-part-1" in flagged_before

    client.post(
        "/api/v1/norms/uk-rf:art-158-part-1/confirm-history",
        json={"checked_through": "2026-09-28", "source": "КонсультантПлюс"},
        headers=admin_headers,
    )

    after = client.get("/api/v1/norms/coverage").json()
    flagged_after = {n["norm_id"] for n in after["norms"] if n["needs_review"]}
    assert "uk-rf:art-158-part-1" not in flagged_after


def test_judge_cannot_confirm_history(edit_setup) -> None:
    client, admin_headers, _ = edit_setup
    judge_headers = _judge_headers(client, admin_headers)

    response = client.post(
        "/api/v1/norms/uk-rf:art-158-part-1/confirm-history",
        json={"checked_through": "2026-09-28", "source": "КонсультантПлюс"},
        headers=judge_headers,
    )

    assert response.status_code == 403


def test_anonymous_cannot_confirm_history(edit_setup) -> None:
    client, _admin_headers, _ = edit_setup

    response = client.post(
        "/api/v1/norms/uk-rf:art-158-part-1/confirm-history",
        json={"checked_through": "2026-09-28", "source": "КонсультантПлюс"},
    )

    assert response.status_code == 403


def test_confirm_history_rejects_missing_source(edit_setup) -> None:
    client, admin_headers, _ = edit_setup

    response = client.post(
        "/api/v1/norms/uk-rf:art-158-part-1/confirm-history",
        json={"checked_through": "2026-09-28", "source": "   "},
        headers=admin_headers,
    )

    assert response.status_code == 400


def test_confirm_history_reports_unknown_norm(edit_setup) -> None:
    client, admin_headers, _ = edit_setup

    response = client.post(
        "/api/v1/norms/нет-такой/confirm-history",
        json={"checked_through": "2026-09-28", "source": "КонсультантПлюс"},
        headers=admin_headers,
    )

    assert response.status_code == 400
    assert "не найдена" in response.json()["detail"]



def test_admin_adds_version(edit_setup) -> None:
    client, admin_headers, norms_dir = edit_setup

    response = client.post(
        "/api/v1/norms/uk-rf:art-158-part-1/add-version",
        json={
            "text": "текст редакции 2004 года",
            "effective_from": "2004-01-01",
            "version_id": "v-2004",
        },
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["sealed"] is True

    payload = json.loads((norms_dir / "uk.json").read_text(encoding="utf-8"))
    versions = payload["norms"][0]["versions"]
    assert [v["version_id"] for v in versions] == ["v-1996", "v-2004"]
    added = versions[-1]
    assert added["verification_status"] == "draft"
    assert added["sanctions"] == []
    assert added["sha256"] != UNSEALED, "корпус обязан остаться опечатанным"
    assert versions[0]["effective_to"] == "2003-12-31", "предыдущий интервал закрыт"


def test_add_version_writes_an_audit_entry(edit_setup) -> None:
    from second_opinion.config import load_config

    client, admin_headers, _ = edit_setup

    response = client.post(
        "/api/v1/norms/uk-rf:art-158-part-1/add-version",
        json={
            "text": "текст редакции 2004 года",
            "effective_from": "2004-01-01",
            "version_id": "v-2004",
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    events = [
        json.loads(line)
        for line in load_config().audit_path.read_text(encoding="utf-8").splitlines()
    ]
    entry = next(e for e in events if e["operation"] == "norm_add_version")
    assert entry["input_hash"] == "uk-rf:art-158-part-1"
    assert entry["output_hash"] == "v-2004"
    assert entry["details"]["effective_from"] == "2004-01-01"


def test_added_version_is_usable_without_restart(edit_setup) -> None:
    client, admin_headers, _ = edit_setup

    client.post(
        "/api/v1/norms/uk-rf:art-158-part-1/add-version",
        json={
            "text": "текст редакции 2004 года",
            "effective_from": "2004-01-01",
            "version_id": "v-2004",
        },
        headers=admin_headers,
    )

    detail = client.get(
        "/api/v1/norms/uk-rf:art-158-part-1",
        params={"applicable_at": "2010-01-01"},
    ).json()
    assert detail["version"]["version_id"] == "v-2004"


def test_judge_cannot_add_version(edit_setup) -> None:
    client, admin_headers, _ = edit_setup
    judge_headers = _judge_headers(client, admin_headers)

    response = client.post(
        "/api/v1/norms/uk-rf:art-158-part-1/add-version",
        json={"text": "текст", "effective_from": "2004-01-01"},
        headers=judge_headers,
    )

    assert response.status_code == 403


def test_add_version_rejects_empty_text(edit_setup) -> None:
    client, admin_headers, _ = edit_setup

    response = client.post(
        "/api/v1/norms/uk-rf:art-158-part-1/add-version",
        json={"text": "   ", "effective_from": "2004-01-01"},
        headers=admin_headers,
    )

    assert response.status_code in (400, 422)


def test_add_version_rejects_overlapping_interval(edit_setup) -> None:
    client, admin_headers, _ = edit_setup

    response = client.post(
        "/api/v1/norms/uk-rf:art-158-part-1/add-version",
        json={"text": "текст", "effective_from": "1990-01-01"},
        headers=admin_headers,
    )

    assert response.status_code == 400
    assert "действует с" in response.json()["detail"]



def test_edit_is_refused_on_postgres_backend(edit_setup, monkeypatch) -> None:
    client, admin_headers, _ = edit_setup
    monkeypatch.setenv("SO_NORMS_BACKEND", "postgres")
    monkeypatch.setenv("SO_DATABASE_URL", "postgresql://unused/unused")
    reset_config_cache()

    response = client.post(
        "/api/v1/norms/uk-rf:art-158-part-1/confirm-history",
        json={"checked_through": "2026-09-28", "source": "КонсультантПлюс"},
        headers=admin_headers,
    )

    assert response.status_code == 409

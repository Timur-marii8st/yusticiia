"""Состояние нормативного корпуса через API.

Аудит покрытия до этого жил только в CLI: увидеть, что 15 норм из 27
требуют юридической сверки, можно было лишь из терминала.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from second_opinion.api.routes import create_app
from second_opinion.legal_sources.coverage import analyze_store, needs_legal_review


@pytest.fixture()
def client(pipeline):
    return TestClient(create_app(pipeline))


def test_coverage_is_served(client) -> None:
    response = client.get("/api/norms/coverage")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] > 0
    assert len(body["norms"]) == body["total"]


def test_coverage_route_is_not_shadowed_by_norm_id(client) -> None:
    """«coverage» не должно уехать в /api/norms/{norm_id} как идентификатор."""
    response = client.get("/api/norms/coverage")

    assert response.status_code == 200
    assert "norms" in response.json()


def test_review_count_matches_cli_rule(client, pipeline) -> None:
    """Одно и то же число не должно расходиться между CLI и API.

    Наряд считается общей функцией; своя формула в эндпойнте однажды
    разойдётся с `norms-coverage`, и два разных числа будут называться
    одинаково.
    """
    expected = len(needs_legal_review(analyze_store(pipeline.norm_store)))

    body = client.get("/api/norms/coverage").json()

    assert body["needs_review"] == expected
    assert sum(1 for n in body["norms"] if n["needs_review"]) == expected


def test_unverified_history_is_visible_per_norm(client) -> None:
    body = client.get("/api/norms/coverage").json()
    flagged = [n for n in body["norms"] if n["needs_review"]]

    assert flagged, "в корпусе фикстур есть нормы без зафиксированной истории"
    assert all("ref" in n and n["version_count"] >= 1 for n in flagged)


def test_sealed_corpus_is_reported(client) -> None:
    # Фикстуры в репозитории опечатаны, и это должно быть видно снаружи.
    body = client.get("/api/norms/coverage").json()

    assert body["corpus_sealed"] is True


def test_source_edition_absence_is_not_hidden(client) -> None:
    # Пустой список редакций источника — честный ответ «не записано»,
    # а не признак свежести.
    body = client.get("/api/norms/coverage").json()

    assert all(isinstance(n["source_editions"], list) for n in body["norms"])

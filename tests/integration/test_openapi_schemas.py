"""Схема OpenAPI должна описывать форму ответов, а не «любой объект».

Регрессия: все эндпоинты были объявлены как ``-> dict`` с
``response_model=dict``, и каждый ответ в ``/openapi.json`` выглядел как
``{"type": "object", "additionalProperties": true}``. Swagger не
показывал форму ответа, клиента по спецификации сгенерировать было
нельзя, а расхождение кода со справочником ничем не ловилось.
"""

from __future__ import annotations

import json

import pytest

from second_opinion.api.app import create_app

#: Поля модели User, которым нельзя оказаться в ответе ни одного эндпоинта.
SECRET_FIELDS = ("password_hash", "password_history")


@pytest.fixture()
def schema(pipeline) -> dict:
    return create_app(pipeline, auth_token="test-secret").openapi()


def _json_responses(schema: dict):
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            for code in ("200", "201"):
                content = (
                    operation.get("responses", {}).get(code, {}).get("content", {})
                )
                body = content.get("application/json")
                if body is not None:
                    yield f"{method.upper()} {path}", body.get("schema", {})


def test_every_response_has_a_described_shape(schema: dict) -> None:
    generic = [
        endpoint
        for endpoint, body in _json_responses(schema)
        if not body or body.get("additionalProperties") is True
    ]
    assert not generic, f"ответ описан как «любой объект»: {generic}"


def test_all_endpoints_are_covered(schema: dict) -> None:
    """Проверка выше бессмысленна, если ответов в схеме почти нет."""
    assert len(list(_json_responses(schema))) >= 20


def test_analysis_report_is_published_in_full(schema: dict) -> None:
    """Провенанс обязан доходить до клиента: факты, цитаты, нормы, правила."""
    components = schema["components"]["schemas"]
    assert "AnalysisReport" in components
    for model in ("LegalFact", "Evidence", "RuleEvaluation", "NormRef"):
        assert model in components, model


def test_password_hash_never_appears_in_schema(schema: dict) -> None:
    """Доменная модель User несёт хэш и историю паролей — наружу нельзя."""
    dumped = json.dumps(schema, ensure_ascii=False)
    for field in SECRET_FIELDS:
        assert field not in dumped, f"{field} просочилось в схему API"


def test_user_responses_expose_only_safe_fields(schema: dict) -> None:
    components = schema["components"]["schemas"]
    for model in ("UserResponse", "UserSummary", "MeResponse"):
        properties = set(components[model]["properties"])
        assert "user_id" in properties and "email" in properties
        assert not properties & set(SECRET_FIELDS)

"""Сквозной идентификатор запроса и ротация аудит-журнала.

Конвейер писал request_id в логи и в аудит, но снаружи его было не
узнать: человек, наткнувшийся на ошибку, не мог назвать, какую запись
искать. Заголовок связывает увиденное с записанным.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from second_opinion.api.routes import REQUEST_ID_HEADER, _request_id, create_app
from second_opinion.audit.trail import AuditTrail


def test_response_carries_a_request_id(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    response = client.get("/api/norms")
    assert response.status_code == 200
    assert response.headers.get(REQUEST_ID_HEADER)


def test_client_identifier_is_kept(pipeline) -> None:
    """Свой идентификатор клиента уважается: он уже в его журналах."""
    client = TestClient(create_app(pipeline))
    response = client.get("/api/norms", headers={REQUEST_ID_HEADER: "case-42-trace"})
    assert response.headers[REQUEST_ID_HEADER] == "case-42-trace"


def test_overlong_identifier_is_replaced(pipeline) -> None:
    """Чужое значение попадает в журнал — длину ограничиваем."""
    client = TestClient(create_app(pipeline))
    response = client.get("/api/norms", headers={REQUEST_ID_HEADER: "a" * 200})
    assert response.headers[REQUEST_ID_HEADER] != "a" * 200


def test_unsafe_identifier_is_replaced_before_it_reaches_the_log() -> None:
    """Проверка на уровне функции: HTTP-клиент такие значения не пропустит.

    httpx отказывается отправлять заголовок с переводом строки или
    кириллицей, поэтому проверять поведение через запрос бессмысленно —
    а защита нужна: значение идёт в журнал и в заголовок ответа.
    """

    class _Request:
        def __init__(self, value: str) -> None:
            self.headers = {REQUEST_ID_HEADER: value}

    assert _request_id(_Request("trace-1")) == "trace-1"
    for unsafe in ("строка", "trace\nподделка", "a" * 200, "\t", ""):
        produced = _request_id(_Request(unsafe))
        assert produced != unsafe
        assert produced.isascii() and produced.isprintable()


def test_identifier_reaches_the_audit_record(pipeline, sample_clean_text) -> None:
    client = TestClient(create_app(pipeline))
    created = client.post(
        "/api/documents",
        json={"filename": "c.txt", "text": sample_clean_text},
        headers={REQUEST_ID_HEADER: "trace-ingest-1"},
    )
    assert created.status_code == 200

    events = [e for e in pipeline._audit.recent if e.operation == "document_ingest"]
    assert events and events[-1].request_id == "trace-ingest-1"


def test_unauthorized_response_also_carries_the_identifier(pipeline) -> None:
    """Ошибка — как раз тот случай, когда идентификатор и нужен."""
    client = TestClient(create_app(pipeline, auth_token="secret"))
    response = client.get("/api/norms")
    assert response.status_code == 401
    assert response.headers.get(REQUEST_ID_HEADER)


def test_audit_log_rotates_instead_of_growing_forever(tmp_path) -> None:
    """Журнал закрывается и начинается заново, но записи не пропадают."""
    path = tmp_path / "audit.jsonl"
    trail = AuditTrail(path, max_bytes=400)

    for i in range(12):
        trail.log(operation="probe", component="test", details={"n": i})

    rotated = sorted(tmp_path.glob("audit-*.jsonl"))
    assert rotated, "журнал не ротировался"
    assert path.exists(), "после ротации не начат новый журнал"

    # Ни одна запись не потеряна: старые лежат в закрытых файлах.
    total = sum(1 for f in [*rotated, path] for _ in f.read_text(encoding="utf-8").splitlines())
    assert total == 12


def test_rotation_is_off_when_limit_is_zero(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    trail = AuditTrail(path, max_bytes=0)
    for i in range(20):
        trail.log(operation="probe", component="test", details={"n": i})
    assert not list(tmp_path.glob("audit-*.jsonl"))

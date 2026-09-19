"""Выгрузка заключения в .docx (ФТ-34).

Главное свойство выгрузки — она ничего не добавляет к отчёту. Это то же
заключение, что на экране, разложенное по разделам: вывод, обстоятельства
с цитатами, нормы с редакциями, практика, оговорки. Второе представление
результата, расходящееся с первым, было бы хуже отсутствия выгрузки.
"""

from __future__ import annotations

import io

from docx import Document as DocxDocument
from fastapi.testclient import TestClient

from second_opinion.api.app import create_app
from second_opinion.export import build_docx

DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _text_of(payload: bytes) -> str:
    document = DocxDocument(io.BytesIO(payload))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def test_docx_contains_the_same_conclusion_as_the_report(pipeline, sample_violation_text) -> None:
    document = pipeline.ingest("violation.txt", sample_violation_text.encode("utf-8"))
    report = pipeline.analyze(document.document_id)

    text = _text_of(build_docx(report, document))

    assert "Второе мнение: заключение по проекту судебного акта" in text
    assert "Система не заменяет судью" in text
    assert "Краткое изложение дела" in text
    assert "Проблемные места" in text
    assert report.summary.headline in text

    # Нарушение предела обязано доехать до файла: ради него заключение и печатают.
    violation = next(c for c in report.concerns if c.severity == "violation")
    assert violation.title in text

    # Норма без редакции непроверяема, поэтому редакция едет вместе с нормой.
    versioned = [n for e in report.evaluations for n in e.norms_used if n.version_id]
    assert versioned, "в отчёте нет ни одной нормы с редакцией — проверять нечего"
    assert f"редакция {versioned[0].version_id}" in text

    # Цитаты-доказательства — часть цепочки обоснования, не украшение.
    quoted = next(f for f in report.facts if f.evidence)
    assert quoted.evidence[0].quote in text


def test_docx_survives_a_deleted_document(pipeline, sample_clean_text) -> None:
    """Право на забвение не должно ломать выгрузку уже готового отчёта."""
    document = pipeline.ingest("case.txt", sample_clean_text.encode("utf-8"))
    report = pipeline.analyze(document.document_id)

    text = _text_of(build_docx(report, None))
    assert "Идентификатор анализа" in text
    assert "case.txt" not in text


def test_endpoint_returns_a_docx_file(pipeline, sample_clean_text) -> None:
    client = TestClient(create_app(pipeline))
    created = client.post("/api/v1/documents", json={"filename": "c.txt", "text": sample_clean_text})
    document_id = created.json()["document_id"]
    analysis_id = client.post(f"/api/v1/documents/{document_id}/analyze", json={}).json()[
        "analysis_id"
    ]

    response = client.get(f"/api/v1/analyses/{analysis_id}/document")
    assert response.status_code == 200
    assert response.headers["content-type"] == DOCX_MEDIA_TYPE
    assert "attachment;" in response.headers["content-disposition"]
    # .docx — это zip-контейнер; проверяем сигнатуру, а не только заголовки.
    assert response.content[:2] == b"PK"
    assert "Краткое изложение дела" in _text_of(response.content)


def test_unknown_analysis_gives_404(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    assert client.get("/api/v1/analyses/нет-такого/document").status_code == 404


def test_download_goes_through_api_fetch(pipeline) -> None:
    """Кнопка не должна быть ссылкой: она увела бы запрос мимо авторизации."""
    client = TestClient(create_app(pipeline))
    html = client.get("/").text
    js = client.get("/static/app.js").text

    assert 'id="download-docx"' in html
    assert "apiFetch(`/api/v1/analyses/${analysisId}/document${mask}`)" in js


def test_masked_export_hides_personal_data(pipeline, sample_clean_text) -> None:
    """Обезличивание применяется к выгрузке, а не к хранимому документу (Д-06).

    В заключении персональные данные появляются там, где в цитату попала
    фамилия: сам текст документа в файл не переносится. Поэтому проверка
    строится на обстоятельстве, добавленном судьёй с такой цитатой, —
    это и есть реальный путь утечки при пересылке заключения.
    """
    client = TestClient(create_app(pipeline))
    created = client.post("/api/v1/documents", json={"filename": "c.txt", "text": sample_clean_text})
    document_id = created.json()["document_id"]
    analysis_id = client.post(f"/api/v1/documents/{document_id}/analyze", json={}).json()[
        "analysis_id"
    ]
    added = client.post(
        f"/api/v1/analyses/{analysis_id}/facts",
        json={"type": "defendant_age", "value": 34, "quote": "Смирнова А.В., возраст 34 года"},
    )
    assert added.status_code == 200, added.text

    plain = _text_of(client.get(f"/api/v1/analyses/{analysis_id}/document").content)
    masked = _text_of(client.get(f"/api/v1/analyses/{analysis_id}/document?mask=true").content)

    assert "Смирнова А.В." in plain
    assert "Смирнова А.В." not in masked
    assert "Персональные данные в этом файле скрыты" in masked
    assert "Обнаружено возможных персональных данных" in plain

    # Хранимый документ не изменился: обезличивается то, что уходит.
    stored = client.get(f"/api/v1/documents/{document_id}").json()["text"]
    assert "Смирнова А.В." in stored

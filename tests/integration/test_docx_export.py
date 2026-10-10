"""Выгрузка заключения в .docx (ФТ-34).

Главное свойство выгрузки — она ничего не добавляет к отчёту. Это то же
заключение, что на экране, разложенное по разделам: вывод, обстоятельства
с цитатами, нормы с редакциями, практика, оговорки. Второе представление
результата, расходящееся с первым, было бы хуже отсутствия выгрузки.
"""

from __future__ import annotations

import html
import io

import pytest
from docx import Document as DocxDocument
from docx.oxml.ns import qn
from fastapi.testclient import TestClient

from second_opinion.api.app import create_app
from second_opinion.export import Preparer, build_docx
from second_opinion.reporting import FACT_TYPE_LABELS

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _text_of(payload: bytes) -> str:
    document = DocxDocument(io.BytesIO(payload))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def test_docx_contains_the_same_conclusion_as_the_report(pipeline, sample_violation_text) -> None:
    document = pipeline.ingest("violation.txt", sample_violation_text.encode("utf-8"))
    report = pipeline.analyze(document.document_id)

    text = _text_of(build_docx(report, document))

    assert "Второе мнение: заключение по проекту судебного акта" in text
    assert "Краткое изложение дела" in text
    assert "Проблемные места" in text
    assert report.summary.headline in text

    violation = next(c for c in report.concerns if c.severity == "violation")
    assert violation.title in text

    versioned = [n for e in report.evaluations for n in e.norms_used if n.version_id]
    assert versioned, "в отчёте нет ни одной нормы с редакцией — проверять нечего"
    assert f"редакция {versioned[0].version_id}" in text

    quoted = next(f for f in report.facts if f.evidence)
    assert quoted.evidence[0].quote in text


def test_docx_carries_the_exclusion_reason(pipeline, sample_clean_text) -> None:
    """Мотивировка отклонения (Концепция ВС РФ, риск № 5) — часть заключения, не только аудита."""
    document = pipeline.ingest("case.txt", sample_clean_text.encode("utf-8"))
    report = pipeline.analyze(document.document_id)
    fact = report.facts[0]
    report = pipeline.update_fact(
        report.analysis_id, fact.id, status="NOT_FOUND", reason="ошибка извлечения"
    )

    text = _text_of(build_docx(report, document))
    assert "причина исключения: ошибка извлечения" in text


def test_docx_survives_a_deleted_document(pipeline, sample_clean_text) -> None:
    """Право на забвение не должно ломать выгрузку уже готового отчёта."""
    document = pipeline.ingest("case.txt", sample_clean_text.encode("utf-8"))
    report = pipeline.analyze(document.document_id)

    text = _text_of(build_docx(report, None))
    assert "Идентификатор анализа" in text
    assert "case.txt" not in text


def test_endpoint_returns_a_docx_file(pipeline, sample_clean_text) -> None:
    client = TestClient(create_app(pipeline))
    created = client.post(
        "/api/v1/documents", json={"filename": "c.txt", "text": sample_clean_text}
    )
    document_id = created.json()["document_id"]
    analysis_id = client.post(f"/api/v1/documents/{document_id}/analyze", json={}).json()[
        "analysis_id"
    ]

    response = client.get(f"/api/v1/analyses/{analysis_id}/document")
    assert response.status_code == 200
    assert response.headers["content-type"] == DOCX_MEDIA_TYPE
    assert "attachment;" in response.headers["content-disposition"]
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
    created = client.post(
        "/api/v1/documents", json={"filename": "c.txt", "text": sample_clean_text}
    )
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

    stored = client.get(f"/api/v1/documents/{document_id}").json()["text"]
    assert "Смирнова А.В." in stored


@pytest.mark.parametrize("endpoint", ["document", "print"])
def test_masked_exports_hide_fact_values_and_filenames(
    pipeline, sample_clean_text, endpoint: str
) -> None:
    """Свободный текст факта и имя файла обезличиваются так же, как цитата."""
    client = TestClient(create_app(pipeline))
    filename = "Иванов Иван Иванович.txt"
    value = "Смирнов Алексей Викторович имеет заболевание."
    source = sample_clean_text + "\n" + value
    created = client.post(
        "/api/v1/documents", json={"filename": filename, "text": source}
    )
    document_id = created.json()["document_id"]
    analysis_id = client.post(f"/api/v1/documents/{document_id}/analyze", json={}).json()[
        "analysis_id"
    ]
    added = client.post(
        f"/api/v1/analyses/{analysis_id}/facts",
        json={"type": "health_factor", "value": value, "quote": value},
    )
    assert added.status_code == 200, added.text
    fact_id = added.json()["facts"][-1]["id"]

    plain = client.get(f"/api/v1/analyses/{analysis_id}/{endpoint}")
    masked = client.get(f"/api/v1/analyses/{analysis_id}/{endpoint}?mask=true")
    assert plain.status_code == masked.status_code == 200
    plain_text = _text_of(plain.content) if endpoint == "document" else html.unescape(plain.text)
    masked_text = _text_of(masked.content) if endpoint == "document" else html.unescape(masked.text)

    assert filename in plain_text
    assert value in plain_text
    assert "Иванов Иван Иванович" not in masked_text
    assert "Смирнов Алексей Викторович" not in masked_text
    assert "[Ф. И.].txt" in masked_text
    assert "[Ф. С.] имеет заболевание." in masked_text
    stored_document = client.get(f"/api/v1/documents/{document_id}").json()
    stored_report = client.get(f"/api/v1/analyses/{analysis_id}").json()
    stored_fact = next(fact for fact in stored_report["facts"] if fact["id"] == fact_id)
    assert stored_document["text"] == source
    assert stored_document["filename"] == filename
    assert stored_fact["value"] == value
    assert stored_fact["evidence"][0]["quote"] == value


def _analysis(client: TestClient, text: str) -> str:
    created = client.post("/api/v1/documents", json={"filename": "c.txt", "text": text})
    document_id = created.json()["document_id"]
    return client.post(f"/api/v1/documents/{document_id}/analyze", json={}).json()["analysis_id"]


def test_docx_has_no_rule_under_the_title_and_no_disclaimers(
    pipeline, sample_violation_text
) -> None:
    document = pipeline.ingest("v.txt", sample_violation_text.encode("utf-8"))
    report = pipeline.analyze(document.document_id)

    payload = build_docx(report, document)
    text = _text_of(payload)
    styles = DocxDocument(io.BytesIO(payload)).styles

    for name in ("Title", "Heading 1"):
        assert (
            styles[name].element.pPr is None or styles[name].element.pPr.find(qn("w:pBdr")) is None
        )
    assert "Оговорки" not in text
    assert "носит вспомогательный характер" not in text
    for disclaimer in report.disclaimers:
        assert disclaimer not in text


def test_docx_uses_tables_for_every_enumeration_of_the_page(
    pipeline, sample_violation_text
) -> None:
    document = pipeline.ingest("v.txt", sample_violation_text.encode("utf-8"))
    report = pipeline.analyze(document.document_id)

    docx = DocxDocument(io.BytesIO(build_docx(report, document)))
    headers = [[cell.text for cell in table.rows[0].cells] for table in docx.tables]
    flat = [cell for row in headers for cell in row]

    for expected in (
        "Показатель",
        "Обстоятельств в основе",
        "Проверка",
        "Использованные факты",
        "Обстоятельство",
        "Цитата в документе",
        "Учитывается в проверках",
        "Признак в выборке",
        "Вид наказания",
    ):
        assert expected in flat, expected
    assert not [p for p in docx.paragraphs if p.style.name.startswith("List")]


def test_docx_carries_all_the_analytics_of_the_page(pipeline, sample_violation_text) -> None:
    document = pipeline.ingest("v.txt", sample_violation_text.encode("utf-8"))
    report = pipeline.analyze(document.document_id)
    analytics = report.analytics
    assert analytics is not None, "в отчёте нет аналитики — проверять нечего"

    text = _text_of(build_docx(report, document))

    for label in (
        "Дел в выборке",
        "Из них синтетических (учебные примеры)",
        "Дел с назначенным сроком лишения свободы",
        "Медиана срока, мес.",
        "Средний срок, мес.",
        "25-й перцентиль, мес.",
        "75-й перцентиль, мес.",
        "Доля условного осуждения",
        "Статистика описательная и не является рекомендацией о наказании.",
    ):
        assert label in text, label
    if analytics.sentence_position is not None:
        assert analytics.sentence_position.note in text
    assert "8. Описательная статистика" in text


def test_docx_summary_names_the_facts_behind_each_line(pipeline, sample_violation_text) -> None:
    document = pipeline.ingest("v.txt", sample_violation_text.encode("utf-8"))
    report = pipeline.analyze(document.document_id)
    line = next(line for line in report.summary.lines if line.fact_ids)
    fact = next(f for f in report.facts if f.id == line.fact_ids[0])

    text = _text_of(build_docx(report, document))

    assert "Обстоятельств в основе" in text
    assert FACT_TYPE_LABELS[fact.type] in text


def test_docx_names_the_cabinet_user_who_ran_the_analysis(pipeline, sample_clean_text) -> None:
    document = pipeline.ingest("c.txt", sample_clean_text.encode("utf-8"))
    report = pipeline.analyze(document.document_id)

    anonymous = _text_of(build_docx(report, document))
    named = _text_of(
        build_docx(report, document, prepared_by=Preparer("Иванов И.И.", "judge@court.ru", "judge"))
    )

    assert "личный кабинет" not in anonymous
    assert "Иванов И.И., Судья, judge@court.ru" in named


def test_docx_is_a_landscape_page(pipeline, sample_clean_text) -> None:
    document = pipeline.ingest("c.txt", sample_clean_text.encode("utf-8"))
    report = pipeline.analyze(document.document_id)

    section = DocxDocument(io.BytesIO(build_docx(report, document))).sections[0]

    assert section.page_width > section.page_height


def test_print_form_has_the_same_content_as_the_docx(pipeline, sample_violation_text) -> None:
    client = TestClient(create_app(pipeline))
    analysis_id = _analysis(client, sample_violation_text)
    report = client.get(f"/api/v1/analyses/{analysis_id}").json()

    response = client.get(f"/api/v1/analyses/{analysis_id}/print")
    docx_text = _text_of(client.get(f"/api/v1/analyses/{analysis_id}/document").content)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    page = html.unescape(response.text)
    assert 'href="/static/print-sheet.css"' in response.text
    assert "<style" not in response.text and "style=" not in response.text
    assert "<script" not in response.text
    assert report["summary"]["headline"] in page
    for heading in ("1. Дело и документ", "4. Проверка норм", "5. Разъяснения и практика Верховного Суда", "8. Описательная статистика"):
        assert heading in page and heading in docx_text
    assert "Оговорки" not in page


def test_print_form_escapes_the_document_text(pipeline, sample_clean_text) -> None:
    client = TestClient(create_app(pipeline))
    analysis_id = _analysis(client, sample_clean_text)
    added = client.post(
        f"/api/v1/analyses/{analysis_id}/facts",
        json={"type": "defendant_age", "value": 34, "quote": "возраст 34 года"},
    )
    assert added.status_code == 200, added.text
    client.patch(
        f"/api/v1/analyses/{analysis_id}/facts/{added.json()['facts'][-1]['id']}",
        json={"status": "NOT_FOUND", "reason": "<script>alert(1)</script>"},
    )

    page = client.get(f"/api/v1/analyses/{analysis_id}/print").text

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_print_form_masks_personal_data_on_request(pipeline, sample_clean_text) -> None:
    client = TestClient(create_app(pipeline))
    analysis_id = _analysis(client, sample_clean_text)
    client.post(
        f"/api/v1/analyses/{analysis_id}/facts",
        json={"type": "defendant_age", "value": 34, "quote": "Смирнова А.В., возраст 34 года"},
    )

    plain = client.get(f"/api/v1/analyses/{analysis_id}/print").text
    masked = client.get(f"/api/v1/analyses/{analysis_id}/print?mask=true").text

    assert "Смирнова А.В." in plain
    assert "Смирнова А.В." not in masked
    assert "Персональные данные в этом файле скрыты" in masked


def test_print_form_of_a_foreign_analysis_is_not_found(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    assert client.get("/api/v1/analyses/нет-такого/print").status_code == 404


def test_exports_carry_the_cabinet_data_of_the_user_who_ran_the_analysis(
    pipeline, monkeypatch, sample_clean_text
) -> None:
    from second_opinion.auth.service import get_auth_service
    from second_opinion.config import load_config

    monkeypatch.setenv("SO_AUTH_MODE", "accounts")
    load_config.cache_clear()
    get_auth_service().create_user("judge@court.ru", "judgepass1234", "Иванов Иван Иванович")
    client = TestClient(create_app(pipeline))
    token = client.post(
        "/auth/login", json={"email": "judge@court.ru", "password": "judgepass1234"}
    ).json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post(
        "/api/v1/documents", json={"filename": "c.txt", "text": sample_clean_text}, headers=headers
    )
    analysis_id = client.post(
        f"/api/v1/documents/{created.json()['document_id']}/analyze", json={}, headers=headers
    ).json()["analysis_id"]

    docx = _text_of(client.get(f"/api/v1/analyses/{analysis_id}/document", headers=headers).content)
    page = html.unescape(client.get(f"/api/v1/analyses/{analysis_id}/print", headers=headers).text)
    load_config.cache_clear()

    expected = "Иванов Иван Иванович, Судья, judge@court.ru"
    assert expected in docx
    assert expected in page


def test_conclusion_carries_supreme_court_positions_for_the_checks_made(pipeline, sample_violation_text) -> None:
    """П. 2.5.1.д Положения: заключение показывает соответствие разъяснениям Пленума,
    решениям Президиума и Обзорам; позиции берутся по связи `applies_to` в корпусе."""
    document = pipeline.ingest("v.txt", sample_violation_text.encode("utf-8"))
    report = pipeline.analyze(document.document_id)
    applicable = {e.rule_id for e in report.evaluations if e.status.value != "NOT_APPLICABLE"}
    assert report.practice_positions
    assert {p.rule_id for p in report.practice_positions} <= applicable
    refs = {p.ref for p in report.practice_positions if p.rule_id == "R-002"}
    assert "п. 34 ППВС № 58" in refs
    assert any(r.startswith("Обзор практики ВС РФ") for r in refs)
    statuses = {p.verification_status for p in report.practice_positions}
    assert statuses <= {"verified", "draft"}

    text = _text_of(build_docx(report, document))
    assert "5. Разъяснения и практика Верховного Суда" in text
    assert "черновик, не сверен юристом" in text

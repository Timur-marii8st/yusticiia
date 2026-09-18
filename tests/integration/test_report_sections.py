"""Краткое изложение дела и проблемные места (ФТ-29, ФТ-31).

Главное свойство обоих разделов — они выводятся из уже показанных данных,
а не добавляют новых утверждений. Изложение собирается из извлечённых
обстоятельств и знает, на каких именно; проблемные места пересказывают
результаты проверок и прямо называют нехватку данных.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from second_opinion.api.routes import create_app


def test_summary_is_assembled_from_extracted_facts(pipeline, sample_clean_text) -> None:
    document = pipeline.ingest("case.txt", sample_clean_text.encode("utf-8"))
    report = pipeline.analyze(document.document_id)

    summary = report.summary
    assert summary.headline, "заголовок изложения пуст"
    assert "158" in summary.headline
    assert summary.lines

    known_ids = {fact.id for fact in report.facts}
    for line in summary.lines:
        assert line.text.strip()
        # Каждая строка держится на фактах отчёта: ссылка на несуществующий
        # факт означала бы, что изложение нельзя раскрыть до цитаты.
        assert set(line.fact_ids) <= known_ids

    # Изложение обязано опираться хоть на что-то, иначе это свободный текст.
    assert any(line.fact_ids for line in summary.lines)


def test_violation_becomes_a_concern(pipeline, sample_violation_text) -> None:
    document = pipeline.ingest("violation.txt", sample_violation_text.encode("utf-8"))
    report = pipeline.analyze(document.document_id)

    violations = [c for c in report.concerns if c.severity == "violation"]
    assert violations, "нарушение предела не попало в проблемные места"
    assert violations[0].rule_id == "R-002"
    assert violations[0].detail

    # Нарушения идут первыми: ограничение данных не должно оттеснять их вниз.
    assert report.concerns[0].severity == "violation"


def test_missing_data_is_named_not_hidden(pipeline, sample_violation_text) -> None:
    document = pipeline.ingest("violation.txt", sample_violation_text.encode("utf-8"))
    report = pipeline.analyze(document.document_id)

    data_concerns = [c for c in report.concerns if c.severity == "data"]
    assert data_concerns, "проверки в статусе UNKNOWN не отражены в проблемных местах"
    # Они собраны в одну строку: семь однотипных замечаний вытеснили бы
    # настоящее нарушение из поля зрения.
    unknown_lines = [c for c in data_concerns if "нехватки данных" in c.title]
    assert len(unknown_lines) == 1


def test_assumed_date_is_flagged(pipeline) -> None:
    """Подставленная дата — допущение, и оно обязано быть названо."""
    text = (
        "ПРИГОВОР (ПРОЕКТ, СИНТЕТИЧЕСКИЙ ОБРАЗЕЦ)\n\n"
        "Подсудимый Иванов И.И., возраст 40 лет.\n"
        "Действия квалифицированы по ч. 1 ст. 158 УК РФ.\n"
        "Назначить наказание в виде лишения свободы на срок 1 год.\n"
    )
    document = pipeline.ingest("no-date.txt", text.encode("utf-8"))
    report = pipeline.analyze(document.document_id)

    assert report.applicable_at_assumed
    assert any("Дата события" in c.title for c in report.concerns)


def test_sections_are_recomputed_after_a_correction(pipeline, sample_violation_text) -> None:
    """После правки факта разделы обязаны совпасть с пересчитанными проверками."""
    document = pipeline.ingest("violation.txt", sample_violation_text.encode("utf-8"))
    report = pipeline.analyze(document.document_id)

    stage_fact = next(f for f in report.facts if f.type == "offense_stage")
    assert any(c.rule_id == "R-002" for c in report.concerns)

    updated = pipeline.update_fact(report.analysis_id, stage_fact.id, status="NOT_FOUND")

    # Стадия исключена — правило ст. 66 больше не применимо, и замечание
    # обязано уйти вместе с ним.
    assert not any(c.rule_id == "R-002" and c.severity == "violation" for c in updated.concerns)
    assert all(
        "покушение" not in line.text for line in updated.summary.lines
    ), "изложение продолжает утверждать то, что судья исключил"


def test_sections_reach_the_interface(pipeline, sample_clean_text) -> None:
    client = TestClient(create_app(pipeline))
    created = client.post("/api/documents", json={"filename": "c.txt", "text": sample_clean_text})
    document_id = created.json()["document_id"]
    report = client.post(f"/api/documents/{document_id}/analyze", json={}).json()

    assert report["summary"]["lines"]
    assert "concerns" in report

    html = client.get("/").text
    js = client.get("/static/app.js").text
    assert 'id="summary-panel"' in html
    assert 'id="concerns-panel"' in html
    assert "renderSummary" in js
    assert "renderConcerns" in js

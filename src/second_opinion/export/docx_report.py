"""Заключение в формате .docx (ФТ-34 из docs/TZ.md).

Печать из браузера уже была, но лист из принтера нельзя приложить к
материалам и нельзя переслать. Судье нужен файл.

Документ собирается из готового отчёта и ничего к нему не добавляет: это
та же цепочка «вывод → факт → цитата → норма → редакция», разложенная по
разделам заключения. Ни одного утверждения, которого нет в отчёте на
экране, здесь появиться не может — иначе выгрузка стала бы вторым,
неконтролируемым представлением результата.

Зависимость `python-docx` уже есть в проекте: ею читаются загружаемые
документы. Новых зависимостей выгрузка не требует.
"""

from __future__ import annotations

import io

from docx import Document as new_document
from docx.document import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from ..domain.analysis import AnalysisReport
from ..domain.documents import Document
from ..domain.enums import RuleStatus
from ..privacy import detect_personal_data, mask_text

STATUS_LABELS = {
    RuleStatus.PASS: "соблюдено",
    RuleStatus.WARNING: "требует внимания",
    RuleStatus.FAIL: "возможно нарушение",
    RuleStatus.UNKNOWN: "недостаточно данных",
}

SEVERITY_LABELS = {
    "violation": "Проверка не сошлась",
    "attention": "Требует внимания",
    "data": "Ограничение данных",
    "statistics": "Статистическое наблюдение",
}


def _heading(doc: DocxDocument, text: str, level: int = 1) -> None:
    doc.add_heading(text, level=level)


def _kv(doc: DocxDocument, label: str, value: str) -> None:
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(f"{label}: ")
    run.bold = True
    paragraph.add_run(value)


def build_docx(
    report: AnalysisReport,
    document: Document | None = None,
    mask_personal_data: bool = False,
) -> bytes:
    """Собрать заключение в .docx и вернуть его байтами.

    ``mask_personal_data`` скрывает персональные данные в цитатах и в
    изложении (Д-06). Оригинал документа при этом не меняется:
    обезличивается то, что уходит из системы, а не то, что хранится.
    """

    def visible(value: str) -> str:
        return mask_text(value) if mask_personal_data else value

    doc = new_document()

    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(12)

    title = doc.add_heading("Второе мнение: заключение по проекту судебного акта", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    warning = doc.add_paragraph()
    warning_run = warning.add_run(
        "Документ носит вспомогательный характер. Система не заменяет судью, "
        "не назначает наказание и не является источником права."
    )
    warning_run.italic = True

    _heading(doc, "1. Дело и документ")
    if document is not None:
        _kv(doc, "Файл", document.filename)
        _kv(doc, "Контрольная сумма документа (SHA-256)", document.sha256)
    _kv(doc, "Идентификатор анализа", report.analysis_id)
    _kv(doc, "Дата анализа", report.created_at.isoformat())
    applicable = report.applicable_at or "не определена"
    if report.applicable_at_assumed:
        applicable += " (дата в документе не найдена, взята текущая — допущение)"
    _kv(doc, "Нормы применены на дату", applicable)
    if document is not None:
        found = detect_personal_data(document.text)
        if found.total:
            _kv(
                doc,
                "Обнаружено возможных персональных данных",
                f"{found.total}"
                + (" (скрыты в этом файле)" if mask_personal_data else " (в файле не скрыты)"),
            )

    _heading(doc, "2. Краткое изложение дела")
    if report.summary.headline:
        headline = doc.add_paragraph()
        headline.add_run(report.summary.headline).bold = True
    if report.summary.lines:
        for line in report.summary.lines:
            doc.add_paragraph(visible(line.text), style="List Bullet")
    else:
        doc.add_paragraph("Обстоятельства из документа не извлечены.")

    _heading(doc, "3. Проблемные места")
    if report.concerns:
        for concern in report.concerns:
            paragraph = doc.add_paragraph(style="List Bullet")
            label = SEVERITY_LABELS.get(concern.severity, concern.severity)
            paragraph.add_run(f"{label}. ").bold = True
            paragraph.add_run(visible(concern.title))
            if concern.detail:
                doc.add_paragraph(visible(concern.detail))
    else:
        doc.add_paragraph("Проверки прошли, данных хватило: отдельных замечаний нет.")

    _heading(doc, "4. Проверка норм")
    for evaluation in report.evaluations:
        paragraph = doc.add_paragraph()
        status = STATUS_LABELS.get(evaluation.status, str(evaluation.status))
        paragraph.add_run(f"{evaluation.headline} — {status}").bold = True
        if evaluation.explanation:
            doc.add_paragraph(visible(evaluation.explanation))
        if evaluation.norms_used:
            # Редакция обязана ехать вместе с нормой: без неё ссылка не
            # проверяема, а проверяемость — смысл всего отчёта.
            norms = "; ".join(
                f"{norm.ref}" + (f" (редакция {norm.version_id})" if norm.version_id else "")
                for norm in evaluation.norms_used
            )
            doc.add_paragraph(f"Применённые нормы: {norms}")
        doc.add_paragraph(f"Правило: {evaluation.rule_id}")

    _heading(doc, "5. Обстоятельства и доказательства")
    for fact in report.facts:
        paragraph = doc.add_paragraph(style="List Bullet")
        paragraph.add_run(f"{fact.type}: ").bold = True
        paragraph.add_run(str(fact.value))
        paragraph.add_run(f" [{fact.status}, метод: {fact.extraction_method}]")
        for evidence in fact.evidence:
            quote = doc.add_paragraph(f"«{visible(evidence.quote)}»")
            quote.paragraph_format.left_indent = Pt(24)
            doc.add_paragraph(
                f"Фрагмент документа: символы {evidence.start_offset}–{evidence.end_offset}"
            ).paragraph_format.left_indent = Pt(24)

    _heading(doc, "6. Сопоставимая практика и статистика")
    if report.comparable_cases:
        doc.add_paragraph(
            "Справочный материал. Сходство не является основанием для вывода о наказании."
        )
        for match in report.comparable_cases:
            case = match.case
            mark = " (синтетическое дело)" if case.synthetic else ""
            term = f"{case.term_months:g} мес." if case.term_months is not None else "—"
            doc.add_paragraph(
                f"{case.title}{mark}: ст. {case.article}"
                + (f" ч. {case.part}" if case.part else "")
                + f", наказание {term}",
                style="List Bullet",
            )
    else:
        doc.add_paragraph("Сопоставимые дела в базе практики не найдены.")

    analytics = report.analytics
    if analytics is not None:
        _kv(doc, "Дел в выборке", str(analytics.n_cases))
        if analytics.median_months is not None:
            _kv(doc, "Медиана срока, мес.", str(analytics.median_months))
        if analytics.sentence_position is not None:
            doc.add_paragraph(analytics.sentence_position.note)

    if mask_personal_data:
        note = doc.add_paragraph()
        note.add_run(
            "Персональные данные в этом файле скрыты автоматически. "
            "Полнота обезличивания не гарантирована: перед передачей "
            "документа проверьте его глазами."
        ).italic = True

    _heading(doc, "7. Оговорки")
    for disclaimer in report.disclaimers:
        doc.add_paragraph(disclaimer, style="List Bullet")

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()

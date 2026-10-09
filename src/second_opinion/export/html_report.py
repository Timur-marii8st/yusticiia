"""Печатная форма заключения: тот же лист, что в .docx, но страницей HTML.

Страница подключает только ``/static/print-sheet.css``: политика безопасности
запрещает встроенные стили. Весь текст экранируется, содержимое отчёта (в том
числе цитаты из документа) разметкой стать не может.
"""

from __future__ import annotations

from html import escape

from ..domain.analysis import AnalysisReport
from ..domain.documents import Document
from .report_sheet import Preparer, Sheet, Table, build_sheet


def _cell(text: str) -> str:
    return "<br>".join(escape(line) for line in text.split("\n"))


def _table_html(table: Table) -> str:
    head = "".join(f"<th>{escape(header)}</th>" for header in table.headers)
    body = []
    for row in table.rows:
        cells = []
        for index, value in enumerate(row):
            tag = "th" if table.key_value and index == 0 else "td"
            cells.append(f"<{tag}>{_cell(value)}</{tag}>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    widths = table.widths or [1.0] * len(table.headers)
    total = sum(widths)
    columns = "".join(f'<col class="w{round(100 * width / total / 5) * 5}">' for width in widths)
    return (
        f"<table><colgroup>{columns}</colgroup><thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def render_html(sheet: Sheet) -> str:
    parts = [
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">',
        f"<title>{escape(sheet.title)}</title>",
        '<link rel="stylesheet" href="/static/print-sheet.css"></head><body>',
        f"<h1>{escape(sheet.title)}</h1>",
    ]
    for block in sheet.blocks:
        if block.kind == "heading":
            parts.append(f"<h2>{escape(block.text)}</h2>")
        elif block.kind == "table" and block.table is not None:
            parts.append(_table_html(block.table))
        else:
            parts.append(f'<p class="{block.kind}">{escape(block.text)}</p>')
    parts.append("</body></html>")
    return "".join(parts)


def build_html(
    report: AnalysisReport,
    document: Document | None = None,
    mask_personal_data: bool = False,
    prepared_by: Preparer | None = None,
) -> str:
    return render_html(build_sheet(report, document, mask_personal_data, prepared_by))

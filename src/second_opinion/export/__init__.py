"""Выгрузка заключения в форматы, пригодные для подшивки в дело."""

from .docx_report import build_docx
from .html_report import build_html
from .report_sheet import Preparer

__all__ = ["Preparer", "build_docx", "build_html"]

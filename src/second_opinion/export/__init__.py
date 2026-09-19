"""Выгрузка заключения в форматы, пригодные для подшивки в дело."""

from .docx_report import build_docx

__all__ = ["build_docx"]

from __future__ import annotations

from pathlib import Path

import pytest

from second_opinion.ingestion.parser import ParseError, parse_document

TEST_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MAX_BYTES = 1_000_000


def test_parse_txt_and_sha256() -> None:
    doc = parse_document("a.txt", "привет, суд".encode(), MAX_BYTES)
    assert doc.content_type == "text/plain"
    assert doc.text == "привет, суд"
    assert len(doc.sha256) == 64


def test_parse_txt_normalizes_newlines() -> None:
    doc = parse_document("a.txt", b"line1\r\nline2\rline3\n", MAX_BYTES)
    assert doc.text == "line1\nline2\nline3\n"


def test_parse_txt_cp1251() -> None:
    doc = parse_document("a.txt", "приговор".encode("cp1251"), MAX_BYTES)
    assert "приговор" in doc.text


def test_parse_markdown() -> None:
    doc = parse_document("a.md", "# Заголовок".encode(), MAX_BYTES)
    assert doc.content_type == "text/markdown"


def test_rejects_unsupported_extension() -> None:
    with pytest.raises(ParseError):
        parse_document("a.exe", b"MZ", MAX_BYTES)


def test_rejects_empty_file() -> None:
    with pytest.raises(ParseError):
        parse_document("a.txt", b"", MAX_BYTES)


def test_rejects_whitespace_only() -> None:
    with pytest.raises(ParseError):
        parse_document("a.txt", b"   \n  ", MAX_BYTES)


def test_rejects_oversized_file() -> None:
    with pytest.raises(ParseError):
        parse_document("a.txt", b"x" * (MAX_BYTES + 1), MAX_BYTES)


def test_rejects_binary_pdf_garbage() -> None:
    with pytest.raises(ParseError):
        parse_document("a.pdf", b"\x00\x01not a pdf", MAX_BYTES)


def test_parse_docx_fixture() -> None:
    content = (TEST_FIXTURES / "sample_text.docx").read_bytes()
    doc = parse_document("sample.docx", content, MAX_BYTES)
    assert "ч. 2 ст. 158 УК РФ" in doc.text
    assert doc.page_offsets == []


def test_parse_pdf_fixture_with_offsets() -> None:
    content = (TEST_FIXTURES / "sample_text.pdf").read_bytes()
    doc = parse_document("sample.pdf", content, MAX_BYTES)
    assert "158" in doc.text
    assert doc.page_for_offset(0) == 1


def test_rejects_pdf_without_text_layer() -> None:
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(ParseError):
        parse_document("scan.pdf", buffer.getvalue(), MAX_BYTES)

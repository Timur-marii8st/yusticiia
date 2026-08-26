from __future__ import annotations

import io

import pytest
from pypdf import PdfWriter

from second_opinion.ingestion.parser import ParseError, parse_document

MAX_BYTES = 1_000_000


def _blank_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_ocr_available_is_bool() -> None:
    from second_opinion.ingestion.parser import _ocr_available

    assert isinstance(_ocr_available(), bool)


def test_parse_pdf_without_text_layer_fallback_to_ocr(monkeypatch) -> None:
    """Скан без текстового слоя: при доступном OCR используется распознанный текст."""
    from second_opinion.ingestion import parser

    monkeypatch.setattr(parser, "_ocr_available", lambda: True)
    monkeypatch.setattr(
        parser, "_ocr_pdf_pages", lambda content: ["распознанный текст ч. 2 ст. 158 УК РФ"]
    )

    doc = parse_document("scan.pdf", _blank_pdf_bytes(), MAX_BYTES)
    assert "ч. 2 ст. 158 УК РФ" in doc.text
    assert doc.content_type == "application/pdf"
    assert doc.page_offsets == [0]


def test_parse_pdf_ocr_empty_still_fails(monkeypatch) -> None:
    from second_opinion.ingestion import parser

    monkeypatch.setattr(parser, "_ocr_available", lambda: True)
    monkeypatch.setattr(parser, "_ocr_pdf_pages", lambda content: ["   ", ""])

    with pytest.raises(ParseError, match="OCR не дал результата"):
        parse_document("scan.pdf", _blank_pdf_bytes(), MAX_BYTES)


def test_parse_pdf_no_ocr_available_keeps_original_error(monkeypatch) -> None:
    from second_opinion.ingestion import parser

    monkeypatch.setattr(parser, "_ocr_available", lambda: False)

    with pytest.raises(ParseError, match="скан без OCR"):
        parse_document("scan.pdf", _blank_pdf_bytes(), MAX_BYTES)


def test_parse_pdf_ocr_exception_propagates(monkeypatch) -> None:
    from second_opinion.ingestion import parser

    monkeypatch.setattr(parser, "_ocr_available", lambda: True)

    def _raise(content: bytes) -> list[str]:
        raise ParseError("tesseract не установлен")

    monkeypatch.setattr(parser, "_ocr_pdf_pages", _raise)

    with pytest.raises(ParseError, match="tesseract"):
        parse_document("scan.pdf", _blank_pdf_bytes(), MAX_BYTES)


@pytest.mark.skipif(
    not __import__("second_opinion.ingestion.parser", fromlist=["_ocr_available"])._ocr_available(),  # type: ignore[attr-defined]
    reason="OCR зависимости не установлены — пропускаем интеграционный тест",
)
def test_real_ocr_on_scanned_image_pdf() -> None:
    """Интеграционный тест: реальный OCR на PDF-картинке, если tesseract доступен."""
    try:
        from PIL import Image, ImageDraw
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError:
        pytest.skip("reportlab/Pillow не установлены")

    # Рисуем картинку с русским текстом
    image = Image.new("RGB", (600, 100), color="white")
    draw = ImageDraw.Draw(image)
    draw.text((10, 30), "Приговор ч. 2 ст. 158 УК РФ", fill="black")
    img_buffer = io.BytesIO()
    image.save(img_buffer, format="PNG")
    img_buffer.seek(0)

    from reportlab.lib.utils import ImageReader

    pdf_buffer = io.BytesIO()
    c2 = canvas.Canvas(pdf_buffer, pagesize=letter)
    c2.drawImage(ImageReader(img_buffer), 50, 700, width=500, height=80)
    c2.showPage()
    c2.save()

    doc = parse_document("ocr_scan.pdf", pdf_buffer.getvalue(), MAX_BYTES)
    # OCR может дать неточную транскрипцию; проверяем, что хоть что-то распознано
    assert doc.text.strip()
    assert len(doc.text) > 5

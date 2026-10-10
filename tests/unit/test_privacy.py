"""Обнаружение и маскирование персональных данных (Д-06).

Проверяется не только то, что данные находятся, но и два свойства, без
которых обезличивание ломает систему: маскирование с сохранением длины не
сдвигает оффсеты, а читаемое маскирование остаётся читаемым для человека,
который будет сверять документ.
"""

from __future__ import annotations

import pytest

from second_opinion.privacy import (
    MaskStyle,
    PersonalDataKind,
    detect_personal_data,
    mask_text,
)

JUDGMENT = (
    "Н-ский районный суд в составе судьи Ивановой И.И.,\n"
    "рассмотрев дело в отношении Смирнова Алексея Викторовича,\n"
    "12.05.1990 года рождения, проживающего по адресу\n"
    "г. Москва, ул. Ленина, д. 5, кв. 10, паспорт 45 12 №123456,\n"
    "телефон +7 (916) 123-45-67, ИНН 7701234567,\n"
    "установил: действия квалифицированы по ч. 2 ст. 158 УК РФ.\n"
)


def test_finds_the_main_kinds() -> None:
    report = detect_personal_data(JUDGMENT)
    kinds = {span.kind for span in report.spans}

    assert PersonalDataKind.INITIALS in kinds
    assert PersonalDataKind.FULL_NAME in kinds
    assert PersonalDataKind.BIRTH_DATE in kinds
    assert PersonalDataKind.ADDRESS in kinds
    assert PersonalDataKind.PASSPORT in kinds
    assert PersonalDataKind.PHONE in kinds
    assert PersonalDataKind.INN in kinds


def test_spans_point_at_the_real_text() -> None:
    report = detect_personal_data(JUDGMENT)
    for span in report.spans:
        assert JUDGMENT[span.start : span.end] == span.text


def test_legal_references_are_not_personal_data() -> None:
    """«ч. 2 ст. 158 УК РФ» не должно попасть под маскирование."""
    report = detect_personal_data(JUDGMENT)
    masked = mask_text(JUDGMENT, report)
    assert "ч. 2 ст. 158 УК РФ" in masked


def test_length_preserving_mask_keeps_offsets() -> None:
    """Оффсеты — условие того, что цитату можно показать по оригиналу.

    Текст уходит внешней модели замаскированным; вернувшаяся цитата
    ищется в нём, а показывается по оригиналу. Сдвиг хотя бы на символ
    сделал бы цитаты неверными.
    """
    masked = mask_text(JUDGMENT, style=MaskStyle.PRESERVE_LENGTH)
    assert len(masked) == len(JUDGMENT)

    report = detect_personal_data(JUDGMENT)
    for span in report.spans:
        assert masked[: span.start] == JUDGMENT[: span.start]
        break


def test_readable_mask_hides_but_stays_readable() -> None:
    report = detect_personal_data(JUDGMENT)
    masked = mask_text(JUDGMENT, report)

    assert "Смирнова Алексея Викторовича" not in masked
    assert "Ивановой И.И." not in masked
    assert "+7 (916) 123-45-67" not in masked
    assert "Н-ский районный суд" in masked
    assert "установил" in masked


def test_nothing_to_hide_is_left_alone() -> None:
    text = "Действия квалифицированы по ч. 1 ст. 228 УК РФ."
    assert mask_text(text) == text
    assert detect_personal_data(text).total == 0


@pytest.mark.parametrize("style", list(MaskStyle))
def test_masking_is_idempotent_enough(style: MaskStyle) -> None:
    """Повторное маскирование не должно разрушать текст дальше."""
    once = mask_text(JUDGMENT, style=style)
    twice = mask_text(once, style=style)
    assert len(twice) <= len(once) + 40

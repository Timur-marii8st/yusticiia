"""Обнаружение и маскирование персональных данных (Д-06 из docs/TZ.md).

В проекте судебного акта персональных данных много: фамилии подсудимого,
потерпевшего, свидетелей и участников процесса, даты рождения, адреса,
реквизиты документов. Система хранит **дословный текст** — иначе нельзя
выполнить инвариант «цитата дословно находится в документе», — поэтому
обезличивание делается не при загрузке, а на выходе: при выгрузке
заключения и перед передачей текста внешней модели.

Два способа маскирования, и разница между ними существенная:

* ``MaskStyle.READABLE`` — «Смирнов А.В.» → «подсудимый С.». Годится для
  файла, который читает человек.
* ``MaskStyle.PRESERVE_LENGTH`` — замена той же длины. Нужна там, где
  важны оффсеты: текст, отданный модели, должен совпадать с оригиналом
  посимвольно, иначе возвращённую цитату негде будет найти, и все
  извлечённые факты отсеются как ненайденные.

**Чего этот модуль не делает.** Полного обезличивания он не гарантирует
и гарантировать не может: пропущенное отчество или редкая фамилия — и
данные ушли. Это помощь человеку, а не замена его решению; правила
деперсонализации судебных актов заданы нормативно, и перечень
исключаемого подтверждает юрист.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field


class PersonalDataKind(StrEnum):
    """Что именно найдено. Вид определяет и подпись при маскировании."""

    FULL_NAME = "full_name"
    INITIALS = "initials"
    BIRTH_DATE = "birth_date"
    ADDRESS = "address"
    PASSPORT = "passport"
    SNILS = "snils"
    INN = "inn"
    PHONE = "phone"


KIND_LABELS = {
    PersonalDataKind.FULL_NAME: "фамилия, имя, отчество",
    PersonalDataKind.INITIALS: "фамилия и инициалы",
    PersonalDataKind.BIRTH_DATE: "дата рождения",
    PersonalDataKind.ADDRESS: "адрес",
    PersonalDataKind.PASSPORT: "паспортные данные",
    PersonalDataKind.SNILS: "СНИЛС",
    PersonalDataKind.INN: "ИНН",
    PersonalDataKind.PHONE: "телефон",
}


class MaskStyle(StrEnum):
    READABLE = "readable"
    PRESERVE_LENGTH = "preserve_length"


class PersonalDataSpan(BaseModel):
    """Найденный фрагмент с персональными данными и его границы."""

    kind: PersonalDataKind
    start: int
    end: int
    text: str

    @property
    def label(self) -> str:
        return KIND_LABELS[self.kind]


class PrivacyReport(BaseModel):
    """Что нашлось в документе."""

    spans: list[PersonalDataSpan] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.spans)

    def by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for span in self.spans:
            counts[span.kind.value] = counts.get(span.kind.value, 0) + 1
        return counts


#: Отчество опознаётся по суффиксу: это отличает «Смирнов Алексей
#: Викторович» от «Судебная коллегия Верховного Суда», где три слова с
#: заглавной буквы стоят подряд, но именем не являются.
#: Хвост `[а-яё]{0,2}` — падежные окончания: в приговоре имя чаще стоит
#: в родительном («Смирнова Алексея Викторовича»), а не в именительном.
_PATRONYMIC = r"[А-ЯЁ][а-яё]+(?:ович|евич|ич|овна|евна|ична|инична)[а-яё]{0,2}"

_PATTERNS: tuple[tuple[PersonalDataKind, re.Pattern[str]], ...] = (
    (
        PersonalDataKind.FULL_NAME,
        re.compile(rf"\b[А-ЯЁ][а-яё]+(?:ов|ев|ин|ын|ский|цкий|ко|ук|ян|ко)[а-яё]*\s+[А-ЯЁ][а-яё]+\s+{_PATRONYMIC}\b"),
    ),
    (
        PersonalDataKind.INITIALS,
        re.compile(r"\b[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ]\.\s?[А-ЯЁ]\."),
    ),
    (
        PersonalDataKind.BIRTH_DATE,
        re.compile(
            r"\b(?:\d{1,2}[./]\d{1,2}[./]\d{4}|\d{1,2}\s+[а-яё]+\s+\d{4}\s*(?:года|г\.))"
            r"\s*(?:года\s+)?рождения\b"
        ),
    ),
    (
        PersonalDataKind.BIRTH_DATE,
        re.compile(r"\bрод(?:ился|илась|\.)\s+\d{1,2}[./]\d{1,2}[./]\d{4}\b"),
    ),
    (
        PersonalDataKind.ADDRESS,
        re.compile(
            r"\b(?:г\.|город)\s*[А-ЯЁ][а-яё\-]+\s*,\s*(?:ул\.|улица|пр\.|проспект|пер\.)"
            r"[^,\n]{1,40},\s*д\.\s*\d+[А-Яа-я]?(?:\s*,\s*кв\.\s*\d+)?"
        ),
    ),
    (
        PersonalDataKind.PASSPORT,
        re.compile(r"\bпаспорт[^\n,;]{0,20}?\d{2}\s?\d{2}\s?№?\s?\d{6}\b", re.IGNORECASE),
    ),
    (
        PersonalDataKind.SNILS,
        re.compile(r"\b\d{3}-\d{3}-\d{3}\s?\d{2}\b"),
    ),
    (
        PersonalDataKind.INN,
        re.compile(r"\bИНН\s*\d{10,12}\b"),
    ),
    (
        PersonalDataKind.PHONE,
        re.compile(r"(?:\+7|\b8)\s?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}\b"),
    ),
)


def detect_personal_data(text: str) -> PrivacyReport:
    """Найти фрагменты с персональными данными.

    Пересечения снимаются в пользу более длинного совпадения: «Смирнов
    Алексей Викторович» полезнее, чем «Смирнов А.» внутри него.
    """
    found: list[PersonalDataSpan] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            found.append(
                PersonalDataSpan(
                    kind=kind, start=match.start(), end=match.end(), text=match.group(0)
                )
            )

    found.sort(key=lambda s: (s.start, -(s.end - s.start)))
    result: list[PersonalDataSpan] = []
    occupied_until = -1
    for span in found:
        if span.start < occupied_until:
            continue
        result.append(span)
        occupied_until = span.end
    return PrivacyReport(spans=result)


def _readable_replacement(span: PersonalDataSpan) -> str:
    """Читаемая замена: фамилия сжимается до первой буквы, остальное — вид."""
    if span.kind in (PersonalDataKind.FULL_NAME, PersonalDataKind.INITIALS):
        surname = span.text.strip().split()[0]
        return f"[Ф. {surname[0]}.]"
    return f"[{span.label} скрыты]"


def mask_text(
    text: str,
    report: PrivacyReport | None = None,
    style: MaskStyle = MaskStyle.READABLE,
) -> str:
    """Заменить найденные персональные данные.

    ``PRESERVE_LENGTH`` сохраняет длину каждого фрагмента, а значит и все
    оффсеты в документе: цитата, возвращённая по замаскированному тексту,
    указывает ровно на тот же фрагмент оригинала.
    """
    report = report or detect_personal_data(text)
    if not report.spans:
        return text

    pieces: list[str] = []
    cursor = 0
    for span in report.spans:
        pieces.append(text[cursor : span.start])
        if style is MaskStyle.PRESERVE_LENGTH:
            # Первая буква оставляется намеренно: сплошная полоса делает
            # текст нечитаемым для человека, который будет сверять вывод.
            head = span.text[0]
            pieces.append(head + "·" * (len(span.text) - 1))
        else:
            pieces.append(_readable_replacement(span))
        cursor = span.end
    pieces.append(text[cursor:])
    return "".join(pieces)

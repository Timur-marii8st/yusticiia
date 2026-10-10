from __future__ import annotations

import hashlib
from datetime import date

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

from .enums import PunishmentType, VerificationStatus

PLENUM_CODE = "ППВС"
REVIEW_CODE = "Обзор"


class SanctionSpec(BaseModel):
    """Санкция: пределы одного вида наказания.

    Срок — в месяцах, обязательные работы — в часах, штраф — в рублях.

    ``effective_from``/``effective_to`` сужают действие записи внутри редакции
    нормы: редакция в корпусе может покрывать годы, за которые штраф или часы
    менялись, а лишение свободы нет. Пустые границы означают «всю редакцию».

    ``pending_review`` помечает пределы, внесённые по тексту ИПС без сверки
    юристом (даже внутри сверенной редакции): проверка, опирающаяся на них,
    говорит об этом в объяснении.
    """

    punishment_type: PunishmentType
    min_months: float | None = Field(default=None, ge=0)
    max_months: float | None = Field(default=None, ge=0)
    min_hours: float | None = Field(default=None, ge=0)
    max_hours: float | None = Field(default=None, ge=0)
    min_rubles: float | None = Field(default=None, ge=0)
    max_rubles: float | None = Field(default=None, ge=0)
    effective_from: date | None = None
    effective_to: date | None = None
    source_edition: str = ""
    pending_review: bool = False

    @model_validator(mode="after")
    def _interval_is_ordered(self) -> SanctionSpec:
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError("санкция: effective_to раньше effective_from")
        return self

    @field_serializer("effective_from", "effective_to")
    def _serialize_dates(self, value: date | None, _info: object) -> str | None:
        return value.isoformat() if value is not None else None

    def applies_on(self, when: date) -> bool:
        """Действует ли запись на дату ``when`` (внутри своей редакции)."""
        if self.effective_from is not None and when < self.effective_from:
            return False
        return self.effective_to is None or when <= self.effective_to


def sanction_for(
    sanctions: list[SanctionSpec], punishment_type: str, when: date
) -> SanctionSpec | None:
    """Запись санкции данного вида, действующая на дату ``when``."""
    return next(
        (
            s
            for s in sanctions
            if s.punishment_type.value == punishment_type and s.applies_on(when)
        ),
        None,
    )


class LegalNorm(BaseModel):
    """Норма права (статья/часть), без привязки к редакции.

    ``search_text`` — название статьи и диспозиция части по тексту источника. У
    сверенных частей статей текст редакции — выверенная запись санкции, без
    описания деяния, и без этого поля норма не находилась бы по словам из
    диспозиции. Поле служит только поиску: в проверки и заключение не идёт.
    """

    norm_id: str
    code: str
    article: int = Field(ge=1)
    part: int | None = Field(default=None, ge=1)
    article_suffix: str | None = None
    part_suffix: str | None = None
    title: str
    search_text: str = ""
    applies_to: list[str] = Field(default_factory=list)
    amendments_checked_through: date | None = None
    amendments_checked_source: str = ""

    @property
    def history_verified(self) -> bool:
        return self.amendments_checked_through is not None

    @property
    def ref(self) -> str:
        """Ссылка на норму в том виде, в каком её пишет юрист.

        Постановления Пленума хранятся в тех же полях, что статьи кодексов:
        ``article`` — номер постановления, ``part`` — номер пункта. Печатать
        их общим шаблоном нельзя: «ППВС РФ ст. 60 ч. 14» — это не ссылка на
        источник, а бессмыслица, потому что речь о пункте 14 постановления
        Пленума № 60. Поля не переименованы намеренно: их покрывает манифест
        целостности корпуса, и переименование сломало бы опечатывание ради
        косметики.

        Позиции Обзоров судебной практики ВС РФ хранятся так же: ``code`` —
        название обзора с датой утверждения Президиумом, ``part`` — номер
        позиции. Подпункты с надстрочным индексом (п. 28¹) — ``part_suffix``.
        """
        number = f"{self.part}.{self.part_suffix}" if self.part_suffix else f"{self.part}"
        if self.code.startswith(PLENUM_CODE):
            point = f"п. {number} " if self.part else ""
            return f"{point}{PLENUM_CODE} № {self.article}"
        if self.code.startswith(REVIEW_CODE):
            return f"{self.code}, п. {number}" if self.part else self.code
        suffix = f".{self.article_suffix}" if self.article_suffix else ""
        part = f" ч. {self.part}" if self.part else ""
        return f"{self.code} ст. {self.article}{suffix}{part}"


class NormVersion(BaseModel):
    """Редакция нормы, действующая в интервале дат."""

    norm_id: str
    version_id: str
    text: str
    sanctions: list[SanctionSpec] = Field(default_factory=list)
    effective_from: date
    effective_to: date | None = None
    source_document: str
    source_url: str = ""
    source_edition: str = ""
    retrieved_at: date
    sha256: str
    verification_status: VerificationStatus = VerificationStatus.DRAFT
    synthetic: bool = False

    @field_validator("text")
    @classmethod
    def _text_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("текст нормы не может быть пустым")
        return value

    @model_validator(mode="after")
    def _checksum_matches(self) -> NormVersion:
        actual = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if actual != self.sha256:
            raise ValueError(
                f"норма {self.norm_id} версия {self.version_id}: "
                "контрольная сумма не совпадает с текстом"
            )
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to раньше effective_from")
        return self

    @field_serializer("effective_from", "effective_to", "retrieved_at")
    def _serialize_dates(self, value: date | None, _info: object) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def compute_sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


class NormRef(BaseModel):
    """Ссылка на норму в конкретной редакции (для отчётов)."""

    norm_id: str
    version_id: str
    ref: str

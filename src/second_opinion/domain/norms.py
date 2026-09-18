from __future__ import annotations

import hashlib
from datetime import date

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

from .enums import PunishmentType, VerificationStatus

#: Код постановлений Пленума ВС РФ в фикстурах корпуса (`code` на уровне файла).
PLENUM_CODE = "ППВС"


class SanctionSpec(BaseModel):
    """Санкция: пределы конкретного вида наказания (в месяцах)."""

    punishment_type: PunishmentType
    min_months: float | None = Field(default=None, ge=0)
    max_months: float | None = Field(default=None, ge=0)


class LegalNorm(BaseModel):
    """Норма права (статья/часть), без привязки к редакции."""

    norm_id: str
    code: str  # «УК РФ», «УПК РФ»
    article: int = Field(ge=1)
    part: int | None = Field(default=None, ge=1)
    title: str
    #: Дата, по которую юрист сверил историю изменений статьи. Отличает
    #: проверенный факт «норма не менялась» от допущения «мы внесли один
    #: текст и считаем, что он действовал всегда».
    amendments_checked_through: date | None = None
    #: Чем сверялась история: КонсультантПлюс, ГАРАНТ, pravo.gov.ru.
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
        """
        if self.code.startswith(PLENUM_CODE):
            point = f"п. {self.part} " if self.part else ""
            return f"{point}{PLENUM_CODE} № {self.article}"
        part = f" ч. {self.part}" if self.part else ""
        return f"{self.code} ст. {self.article}{part}"


class NormVersion(BaseModel):
    """Редакция нормы, действующая в интервале дат."""

    norm_id: str
    version_id: str
    text: str
    sanctions: list[SanctionSpec] = Field(default_factory=list)
    effective_from: date
    effective_to: date | None = None  # None — действует по настоящее время
    source_document: str
    source_url: str = ""
    #: Редакция источника, из которой взят текст, в виде
    #: ``источник:документ:редакция`` — например ``pravo:102041891:365``.
    #: Нужна, чтобы отличать «наш текст соответствует такой-то редакции
    #: источника» от «мы однажды что-то скачали». Пустая строка означает
    #: «не записано»: детектор обновлений тогда честно говорит, что
    #: сравнивать не с чем, а не делает вид, что норма свежая.
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

"""Модели ответов API.

До их появления каждый эндпоинт был объявлен как ``-> dict`` с
``response_model=dict``, и в ``/openapi.json`` все ответы выглядели как
``{"type": "object", "additionalProperties": true}``. Практические
следствия: Swagger не показывал форму ответа, клиент нельзя было
сгенерировать, а расхождение кода со справочником ничем не ловилось.

Модели описывают то, что эндпоинты уже возвращают, — контракт не
меняется, он становится проверяемым.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from ..domain.analysis import AnalysisReport
from ..domain.cases import ComparableCase
from ..domain.norms import LegalNorm, NormVersion
from ..domain.users import UserRole
from ..legal_rag.search import SearchHit

AnalysisReportResponse = AnalysisReport


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    version: str
    auth_mode: str = "open"


class HistogramSnapshot(BaseModel):
    buckets: list[float]
    counts: list[int]
    sum: float
    count: int


class MetricsSnapshot(BaseModel):
    counters: dict[str, int]
    histograms: dict[str, HistogramSnapshot]


class MetricsResponse(BaseModel):
    """При ``SO_METRICS_ENABLED != 1`` возвращается только ``enabled: false``."""

    enabled: bool
    metrics: MetricsSnapshot | None = None



class DocumentCreated(BaseModel):
    document_id: str
    filename: str
    content_type: str
    chars: int


class DocumentContent(BaseModel):
    document_id: str
    filename: str
    text: str
    sha256: str


class DeletedResponse(BaseModel):
    deleted: str



class NormSummary(BaseModel):
    norm_id: str
    ref: str
    title: str


class Page(BaseModel):
    """Окно выдачи для списочных ответов.

    Корпус норм сейчас маленький, и соблазн отдавать его целиком велик.
    Но список растёт по плану (наряд на сверку — 15 норм, дальше обзоры
    практики), а клиент, привыкший к полной выдаче, ломается ровно в тот
    момент, когда список наконец вырос.
    """

    total: int
    limit: int
    offset: int
    has_more: bool


class NormListResponse(BaseModel):
    norms: list[NormSummary]
    page: Page | None = None


class NormCoverageItem(BaseModel):
    """Редакционное покрытие одной нормы."""

    norm_id: str
    ref: str
    version_count: int
    covered_from: date
    covered_to: date | None = None
    rule_relevant: bool
    history_verified: bool
    history_unrecorded: bool
    needs_review: bool
    gaps: list[tuple[date, date]] = []
    source_editions: list[str] = []


class NormCoverageResponse(BaseModel):
    """Состояние нормативного корпуса целиком."""

    total: int
    needs_review: int
    corpus_sealed: bool | None = None
    norms: list[NormCoverageItem]


class NormDetailResponse(BaseModel):
    """Без ``applicable_at`` отдаются все редакции, с ним — одна."""

    norm: LegalNorm
    versions: list[NormVersion] | None = None
    version: NormVersion | None = None


class ConfirmHistoryPayload(BaseModel):
    """История изменений нормы сверена: статья действительно не менялась."""

    checked_through: date
    source: str = Field(min_length=1)


class AddVersionPayload(BaseModel):
    """Новая редакция нормы с фактическими границами действия."""

    text: str = Field(min_length=1)
    effective_from: date
    effective_to: date | None = None
    version_id: str = ""
    source_url: str = ""
    source_edition: str = ""


class NormEditResponse(BaseModel):
    """Итог правки корпуса норм."""

    norm_id: str
    message: str
    sealed: bool



class CaseListResponse(BaseModel):
    total: int
    cases: list[ComparableCase]
    page: Page | None = None


class SearchResponse(BaseModel):
    query: str
    results: list[SearchHit]


class HistoryItem(BaseModel):
    """Строка истории дел: чего достаточно, чтобы узнать своё дело.

    Полный отчёт не вкладывается: история открывается часто, а отчёт
    весит сотни килобайт. За содержанием — ``GET /api/analyses/{id}``.
    """

    analysis_id: str
    document_id: str
    filename: str = ""
    created_at: datetime
    applicable_at: str | None = None
    qualification: str = ""
    facts_total: int = 0
    checks: dict[str, int] = Field(default_factory=dict)


class HistoryResponse(BaseModel):
    scope: str
    total: int
    items: list[HistoryItem]



class UserSummary(BaseModel):
    """Пользователь в ответе входа: без служебных полей и без хэша."""

    user_id: str
    email: str
    full_name: str
    role: UserRole


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str | None = Field(
        default=None, description="пусто, если refresh выдан в HttpOnly cookie"
    )
    expires_in: int = Field(description="секунды до истечения access-токена")


class LoginResponse(BaseModel):
    user: UserSummary
    tokens: TokenPairResponse


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str | None = Field(
        default=None, description="пусто, если refresh выдан в HttpOnly cookie"
    )
    expires_in: int


class MeResponse(BaseModel):
    user_id: str
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    mfa_enabled: bool = False


class UserResponse(BaseModel):
    """Пользователь в admin-ответах: с датами, но без пароля и его истории."""

    user_id: str
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    last_login: datetime | None = None
    mfa_enabled: bool = False


class MessageResponse(BaseModel):
    message: str


class MfaSetupResponse(BaseModel):
    secret: str = Field(description="секрет для ручного ввода в приложение-аутентификатор")
    otpauth_uri: str = Field(description="адрес otpauth:// для импорта секрета")


class LoginAttemptResponse(BaseModel):
    attempt_id: str
    email: str
    user_id: str | None = None
    success: bool
    reason: str
    created_at: datetime


__all__ = [
    "AnalysisReportResponse",
    "CaseListResponse",
    "DeletedResponse",
    "DocumentContent",
    "DocumentCreated",
    "HealthResponse",
    "HistogramSnapshot",
    "HistoryItem",
    "HistoryResponse",
    "LoginAttemptResponse",
    "LoginResponse",
    "MeResponse",
    "MessageResponse",
    "MfaSetupResponse",
    "MetricsResponse",
    "MetricsSnapshot",
    "NormCoverageItem",
    "NormCoverageResponse",
    "NormDetailResponse",
    "NormListResponse",
    "NormSummary",
    "Page",
    "RefreshResponse",
    "SearchResponse",
    "TokenPairResponse",
    "UserResponse",
    "UserSummary",
]

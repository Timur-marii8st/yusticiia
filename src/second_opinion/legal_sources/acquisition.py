"""Получение УК РФ для поиска и анализа, без записи в проверенный корпус."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import uuid
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from ..domain.norms import NormVersion
from ..domain.source_fragments import NormSourceState, SourceFragment
from .freshness import _pending_editions, parse_source_edition
from .pravo_gov import (
    Edition,
    EditionListMissing,
    PravoGovSource,
    PravoResponseError,
    prepared_editions,
)
from .redaction_source import RedactionSourceError
from .store import NoApplicableVersionError, NormStore

UK_DOCUMENT = "102041891"


class CachedDocument(BaseModel):
    document_id: str = UK_DOCUMENT
    edition_id: int | None = None
    editions: list[Edition] = Field(default_factory=list)
    articles: dict[str, str] = Field(default_factory=dict)
    sha256: str
    retrieved_at: datetime
    last_checked_at: datetime

    @model_validator(mode="after")
    def integrity(self) -> CachedDocument:
        if self.sha256 != self.digest(self.editions, self.articles):
            raise ValueError("контрольная сумма кэша источника не совпадает")
        if self.edition_id is not None and not self.articles:
            raise ValueError("пустой документ источника")
        return self

    @staticmethod
    def digest(editions: list[Edition], articles: dict[str, str]) -> str:
        content = {"editions": [e.model_dump(mode="json") for e in editions], "articles": articles}
        return hashlib.sha256(
            json.dumps(content, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()


class SourceCache:
    """Атомарные head-файлы и неизменяемые снимки, отдельно от манифеста норм."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def load(self, key: str) -> CachedDocument | None:
        path = self.directory / f"{key}.json"
        if not path.exists():
            return None
        record = CachedDocument.model_validate_json(path.read_text(encoding="utf-8"))
        expected = int(key.removeprefix("edition-")) if key.startswith("edition-") else None
        if record.document_id != UK_DOCUMENT or record.edition_id != expected:
            raise ValueError("координаты документа/редакции в кэше не совпадают с запросом")
        return record

    def save(self, key: str, record: CachedDocument) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        # Снимки включают метаданные времени, их старые версии не перезаписываются.
        payload = record.model_dump_json(indent=2)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        self._atomic(self.directory / f"snapshot-{digest}.json", payload)
        self._atomic(self.directory / f"{key}.json", payload)

    @staticmethod
    def _atomic(path: Path, payload: str) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


class NormAcquisition:
    """Один сервис на процесс; TTL и cooldown общие для всех статей УК РФ.

    ИПС не устанавливает юридические интервалы. Они берутся только из
    подготовленного и подтверждённого локального корпуса. Текст ИПС без
    такого основания всегда остаётся кандидатом, включая запросы в прошлое.
    """

    def __init__(
        self,
        store: NormStore,
        cache: SourceCache,
        source: PravoGovSource | None = None,
        *,
        ttl_seconds: float = 86400,
        failure_ttl_seconds: float = 60,
    ) -> None:
        if (
            not math.isfinite(ttl_seconds)
            or not math.isfinite(failure_ttl_seconds)
            or ttl_seconds < 0
            or failure_ttl_seconds <= 0
        ):
            raise ValueError(
                "TTL источников должен быть неотрицательным; TTL ошибок — положительным"
            )
        self.store = store
        self.cache = cache
        self.source = source
        self.ttl = timedelta(seconds=ttl_seconds)
        self.failure_ttl = failure_ttl_seconds
        self._lock = threading.RLock()
        self._failure: tuple[datetime, PravoResponseError] | None = None
        self._batch: dict[str, tuple[CachedDocument, Literal["cache", "fetched"]]] | None = None

    def resolve_many(
        self, requests: list[tuple[str, str, int | None]], when: date
    ) -> list[NormSourceState]:
        """Одна история и один текст редакции на анализ, даже при TTL=0."""
        with self._lock:
            self._batch = {}
            try:
                return [self._resolve(*request, when) for request in dict.fromkeys(requests)]
            finally:
                self._batch = None

    def resolve(
        self,
        code: str,
        article: str,
        part: int | None,
        when: date,
    ) -> NormSourceState:
        with self._lock:
            return self._resolve(code, article, part, when)

    def _resolve(self, code: str, article: str, part: int | None, when: date) -> NormSourceState:
        number, _, suffix = article.partition(".")
        ref = f"{code} ст. {article}" + (f" ч. {part}" if part else "")
        state = NormSourceState(code=code, article=article, part=part, ref=ref, applicable_at=when)
        norm = self.store.find_by_article(code, int(number), part, suffix or None)
        version = None
        if norm is None:
            state.reasons.append(
                "В локальном корпусе нет нормы с этими реквизитами; подтверждённые нормативные данные отсутствуют."
            )
        if norm:
            state.norm_id = norm.norm_id
            try:
                version = self.store.get_norm(norm.norm_id, when)
            except NoApplicableVersionError:
                state.reasons.append("В локальной истории нет редакции на указанную дату.")
        if version and norm:
            # Закрытый проверенный интервал либо сверка истории до даты запроса.
            confirmed = version.verification_status.value == "verified" and (
                version.effective_to is not None
                or (
                    norm.amendments_checked_through is not None
                    and norm.amendments_checked_through >= when
                )
            )
            state.applicability = "confirmed" if confirmed else "unknown"
            state.structured_data = (
                "verified"
                if version.sanctions
                and version.verification_status.value == "verified"
                and not any(s.pending_review for s in version.sanctions if s.applies_on(when))
                else "pending"
                if version.sanctions
                else "missing"
            )
            for sanction in version.sanctions:
                if sanction.applies_on(when):
                    target = (
                        state.pending_punishment_types
                        if sanction.pending_review
                        or version.verification_status.value != "verified"
                        else state.verified_punishment_types
                    )
                    target.append(sanction.punishment_type.value)
            if state.structured_data == "pending":
                state.reasons.append(
                    "Структурированные санкции или текст редакции не прошли юридическую сверку."
                )
            stamp = parse_source_edition(version.source_edition)
            state.local_fragment = SourceFragment(
                provider=stamp.source if stamp else "local",
                document_id=stamp.document if stamp else norm.code,
                edition_id=stamp.edition if stamp else version.version_id,
                norm_id=norm.norm_id,
                version_id=version.version_id,
                article=article,
                part=part,
                scope="local_norm",
                text=version.text,
                source_url=version.source_url,
                sha256=version.sha256,
                retrieved_at=version.retrieved_at,
                effective_from=version.effective_from,
                effective_to=version.effective_to,
                applicability=state.applicability,
                verification_status=version.verification_status.value,
                synthetic=version.synthetic,
            )
            if not confirmed:
                state.reasons.append(
                    "Применимость локальной редакции на дату дела не подтверждена сверкой истории."
                )
            # Закрытый подтверждённый период не требует проверки текущего текста.
            if confirmed and version.effective_to is not None:
                state.freshness = "confirmed"
                return state
        if self.source is None or code != "УК РФ" or (version and version.synthetic):
            state.acquisition = "offline"
            state.freshness = "offline"
            state.reasons.append(
                "Внешнее получение отключено или не поддерживается для этого кодекса; актуальность не проверена."
            )
            return state

        # Текст/история кэшируются целым документом: правила не делают сетевых запросов.
        now = datetime.now(UTC)
        cached_text = None
        cached_history = None
        try:
            cached_history = self.cache.load("history")
            history, history_mode = self._document("history", None, now)
            prepared = prepared_editions(history.editions)
            if not prepared:
                state.acquisition = "edition_uncertain"
                state.reasons.append(
                    "У источника нет подготовленной редакции; даты вступления в силу не установлены."
                )
                return state
            latest = prepared[-1].number
            assert latest is not None
            pending = _pending_editions(history.editions, latest)
            stamp = parse_source_edition(version.source_edition) if version else None
            # Исторический документ запрашивается по подтверждённому соответствию,
            # никогда по дате изменяющего акта из списка ИПС.
            edition = (
                int(stamp.edition)
                if (
                    state.applicability == "confirmed"
                    and stamp
                    and stamp.source == "pravo"
                    and stamp.document == UK_DOCUMENT
                    and stamp.edition.isdigit()
                )
                else latest
            )
            if edition not in {e.number for e in prepared}:
                state.acquisition = "edition_uncertain"
                state.reasons.append(
                    "Записанная редакция отсутствует среди подготовленных редакций источника."
                )
                return state
            key = f"edition-{edition}"
            cached_text = self.cache.load(key)
            document, mode = self._document(key, edition, now)
            state.acquisition = "fetched" if "fetched" in (history_mode, mode) else "cache"
            candidate_text = document.articles.get(article)
            if not candidate_text:
                state.acquisition = "not_found"
                state.reasons.append(
                    f"Статья {article} не найдена в полном тексте редакции {edition}; это не доказывает её отсутствие на дату дела."
                )
                return state
            entry = next(e for e in prepared if e.number == edition)
            state.candidate = self._fragment(document, article, part, entry)
            state.reasons.append(
                "Текст ИПС получен как кандидат: дата акта не устанавливает дату вступления в силу, санкции требуют отдельной сверки."
            )
            if part:
                state.reasons.append(
                    "Для сверки запрошенной части сохранена вся статья; автоматическое выделение части не подтверждено."
                )
            if pending:
                state.reasons.append(
                    "Есть неподготовленные редакции или неизвестный порядок: "
                    + "; ".join(e.label for e in pending)
                )
            if (
                state.applicability == "confirmed"
                and stamp
                and stamp.edition == str(latest)
                and not pending
            ):
                state.freshness = "confirmed"
            elif version:
                state.reasons.append(
                    "Актуальность основания не подтверждена: изменение документа не доказывает изменения этой статьи."
                )
        except (
            EditionListMissing,
            ValidationError,
            ValueError,
            OSError,
            RedactionSourceError,
        ) as exc:
            kind = (
                exc.kind
                if isinstance(exc, PravoResponseError)
                else "invalid_response"
                if isinstance(exc, EditionListMissing)
                else "cache_error"
                if isinstance(exc, (ValidationError, ValueError, OSError))
                else "unavailable"
            )
            state.acquisition = kind  # type: ignore[assignment]
            state.reasons.append(str(exc))
            if isinstance(exc, RedactionSourceError):
                failure = (
                    exc
                    if isinstance(exc, PravoResponseError)
                    else PravoResponseError(str(exc), kind)
                )
                cooldown = failure.retry_after if kind == "rate_limited" else self.failure_ttl
                self._failure = (now + timedelta(seconds=cooldown), failure)
            # Устаревший снимок доступен для сверки, но ошибка не становится успехом.
            if cached_text is None and cached_history:
                previous = prepared_editions(cached_history.editions)
                if previous:
                    with suppress(ValidationError, ValueError, OSError):
                        cached_text = self.cache.load(f"edition-{previous[-1].number}")
            if cached_text and article in cached_text.articles:
                old_entry = (
                    next(
                        (e for e in cached_history.editions if e.number == cached_text.edition_id),
                        None,
                    )
                    if cached_history
                    else None
                )
                state.candidate = self._fragment(cached_text, article, part, old_entry)
                state.reasons.append(
                    "Показан прежний снимок кэша; повторная проверка источника не удалась."
                )
        return state

    def _fragment(
        self, document: CachedDocument, article: str, part: int | None, entry: Edition | None
    ) -> SourceFragment:
        assert self.source is not None and document.edition_id is not None
        text = document.articles[article]
        return SourceFragment(
            provider="pravo",
            document_id=UK_DOCUMENT,
            edition_id=str(document.edition_id),
            article=article,
            part=part,
            text=text,
            sha256=NormVersion.compute_sha256(text),
            source_url=self.source.edition_url(UK_DOCUMENT, document.edition_id),
            retrieved_at=document.retrieved_at,
            last_checked_at=document.last_checked_at,
            act_date=entry.act_date if entry else None,
            edition_label=entry.label if entry else "",
            amending_act=entry.act_name if entry else "",
        )

    def _document(
        self,
        key: str,
        edition: int | None,
        now: datetime,
    ) -> tuple[CachedDocument, Literal["cache", "fetched"]]:
        if self._batch is not None and key in self._batch:
            return self._batch[key]
        existing = self.cache.load(key)
        if existing and now - existing.last_checked_at < self.ttl:
            if self._batch is not None:
                self._batch[key] = (existing, "cache")
            return existing, "cache"
        if self._failure and self._failure[0] > now:
            raise self._failure[1]
        assert self.source is not None
        editions = self.source.editions(UK_DOCUMENT) if edition is None else []
        articles = self.source.article_texts(UK_DOCUMENT, edition) if edition is not None else {}
        digest = CachedDocument.digest(editions, articles)
        record = CachedDocument(
            edition_id=edition,
            editions=editions,
            articles=articles,
            sha256=digest,
            retrieved_at=existing.retrieved_at if existing and digest == existing.sha256 else now,
            last_checked_at=now,
        )
        self.cache.save(key, record)
        self._failure = None
        if self._batch is not None:
            self._batch[key] = (record, "fetched")
        return record, "fetched"

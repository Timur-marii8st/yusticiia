"""Клиент ИПС «Законодательство России» (`pravo.gov.ru/proxy/ips/`).

Официальный государственный источник. В отличие от ГАРАНТ, доступен без
токена и без подписки, и в отличие от RusLawOD хранит **консолидированные
редакции**, а не только исходные: для УК РФ это 366 записей — исходная и
365 изменяющих актов.

Что источник отдаёт:

* список редакций документа — номер, изменяющий акт и его дата;
* полный текст документа в любой подготовленной редакции.

Три особенности, из-за которых данные нельзя брать как есть.

**Дата в списке — дата акта, а не дата вступления редакции в силу.**
Закон от 04.08.2026 может вводиться в действие позже. Поэтому границы
редакций отсюда — заготовка для юриста, а не готовый ``effective_from``.

**Часть редакций помечена «не готова»** — консолидированный текст не
подготовлен, и в разметке у них нет номера (``value='n'``), то есть
запросить их нельзя. Для УК РФ таких 154 из 366. Пробел источника
скрывать нельзя: он попадает в ``uncertain``.

**Полный текст приходит только с ``fulltext=1``.** Без этого параметра
сервер отдаёт начальный кусок, а параметр ``page`` игнорирует: страница
дочитывается скриптом в браузере. Запрос без ``fulltext=1`` молча
обрывает документ на середине, и сравнение статей даёт ложный ответ.
"""

from __future__ import annotations

import html as html_module
import logging
import re
import time
from datetime import date
from typing import Any

from pydantic import BaseModel

from .redaction_source import (
    ChangingDocument,
    DocumentRedactions,
    Redaction,
    RedactionSourceError,
    UncertainPeriod,
)

logger = logging.getLogger(__name__)

ENCODING = "windows-1251"

_RETRYABLE_STATUSES = frozenset({204, 429, 502, 503, 504})

_OPTION = re.compile(r"<option value='(?P<value>[^']*)'[^>]*>(?P<text>[^<]*)</option>")

_ACT = re.compile(r"от\s+(?P<day>\d{2})\.(?P<month>\d{2})\.(?P<year>\d{4})\s+№\s+(?P<name>[^\s(]+)")

_ARTICLE = re.compile(r"\n[ \t\r]*Статья\s+(\d+)(?:(?:[.\-]|\s+)(\d+))?\s*\.(?=\s)")

_COMPLETED = re.compile(r"textCompleted\s*=\s*'(?P<value>\w+)'")



class EditionListMissing(RedactionSourceError):
    """На странице документа нет списка редакций: чаще сбой источника, реже неверный ``nd``."""


class Edition(BaseModel):
    """Одна запись из списка редакций документа."""

    number: int | None = None
    act_date: date | None = None
    act_name: str = ""
    label: str = ""

    @property
    def prepared(self) -> bool:
        return self.number is not None


class ArticleVerdict(BaseModel):
    """Вывод по одной статье: менялся ли её текст между редакциями."""

    article: str
    changed: bool | None = None
    note: str = ""


class AmendmentReport(BaseModel):
    """Результат сверки статей по двум редакциям документа."""

    topic: str
    editions_total: int
    editions_unprepared: int
    compared_from: str
    compared_to: str
    verdicts: list[ArticleVerdict]


def prepared_editions(editions: list[Edition]) -> list[Edition]:
    """Подготовленные редакции по возрастанию номера.

    Сортировка именно по номеру, а не по дате акта: в выдаче ИПС даты идут
    не по порядку (редакция 355 датирована 27.10.2025, а 353 — 17.11.2025),
    потому что нумерация отражает очерёдность внесения изменений, а не даты
    подписания законов. Брать «первую» и «последнюю» по списку без явной
    сортировки — молчаливое допущение о порядке чужой разметки.
    """
    return sorted(
        (e for e in editions if e.prepared), key=lambda e: e.number or 0
    )


def _strip_tags(payload: str) -> str:
    text = re.sub(r"<[^>]+>", "\n", payload)
    text = html_module.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text)


def parse_editions(payload: str) -> list[Edition]:
    """Разобрать список редакций со страницы документа.

    Страница содержит один и тот же список дважды (просмотр и сравнение),
    поэтому повторы отбрасываются по подписи.
    """
    editions: list[Edition] = []
    seen: set[str] = set()

    for match in _OPTION.finditer(payload):
        label = html_module.unescape(match.group("text")).strip()
        if not label or label in seen:
            continue
        seen.add(label)

        value = match.group("value")
        number: int | None = None
        head = value.split(",", 1)[0]
        if head.isdigit():
            number = int(head)

        act = _ACT.search(label)
        act_date: date | None = None
        act_name = ""
        if act:
            act_name = act.group("name")
            try:
                act_date = date(
                    int(act.group("year")), int(act.group("month")), int(act.group("day"))
                )
            except ValueError:
                logger.warning("ИПС: не удалось разобрать дату акта в %r", label)

        editions.append(
            Edition(number=number, act_date=act_date, act_name=act_name, label=label)
        )

    if not editions:
        raise EditionListMissing(
            "ИПС: на странице документа не найден список редакций — "
            "возможно, изменилась разметка либо неверный идентификатор документа"
        )
    return editions


def extract_articles(payload: str) -> dict[str, str]:
    """Достать тексты статей из документа.

    Ключ — номер статьи («66», «228.1»). ИПС печатает номер с надстрочной
    цифрой по-разному: «Статья 228 1 .» в новых редакциях, «Статья 228-1.» в
    редакциях до 2019 года, где перед словом «Статья» к тому же стоит пробел.
    Без учёта этого статьи старых редакций сливались с соседними, и история
    показывала изменения, которых не было. Пробелы нормализуются: различие в
    переносах не должно читаться как правка.
    """
    text = _strip_tags(payload)
    parts = _ARTICLE.split(text)
    articles: dict[str, str] = {}
    for index in range(1, len(parts) - 2, 3):
        number = parts[index] + (f".{parts[index + 1]}" if parts[index + 1] else "")
        body = " ".join(parts[index + 2].split())
        articles.setdefault(number, body)
    return articles


def is_complete(payload: str) -> bool:
    """Дочитан ли документ до конца.

    Обрыв текста выглядит как отсутствие статьи, а не как ошибка, поэтому
    признак проверяется явно.
    """
    found = _COMPLETED.search(payload)
    if found is None:
        return False
    return found.group("value").lower() == "true"


class PravoGovSource:
    """Редакционная история и тексты редакций из ИПС pravo.gov.ru."""

    name = "pravo"

    def __init__(
        self,
        base_url: str = "http://pravo.gov.ru/proxy/ips/",
        timeout: float = 180.0,
        retries: int = 3,
        backoff: float = 10.0,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError(
                f"SO_PRAVO_BASE_URL должен начинаться с http:// или https:// "
                f"(получено: {base_url!r})"
            )
        self._base_url = base_url
        self._timeout = timeout
        self._retries = retries
        self._backoff = backoff

    def _get(self, params: dict[str, Any], attempt: int = 0) -> str:
        import httpx

        try:
            response = httpx.get(
                self._base_url,
                params=params,
                timeout=self._timeout,
                follow_redirects=True,
            )
        except httpx.TimeoutException as exc:
            raise RedactionSourceError(
                f"ИПС не ответил за {self._timeout:.0f} с ({self._base_url}). "
                "Сервер отвечает медленно, особенно на полный текст кодекса. "
                "Увеличьте таймаут: SO_PRAVO_TIMEOUT=600"
            ) from exc
        except httpx.TransportError as exc:
            if attempt < self._retries:
                time.sleep(self._backoff * (attempt + 1))
                return self._get(params, attempt + 1)
            raise RedactionSourceError(
                f"ИПС недоступен ({self._base_url}): {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RedactionSourceError(
                f"ИПС недоступен ({self._base_url}): {exc}"
            ) from exc

        if response.status_code in _RETRYABLE_STATUSES and attempt < self._retries:
            time.sleep(self._backoff * (attempt + 1))
            return self._get(params, attempt + 1)
        if response.status_code != 200:
            raise RedactionSourceError(
                f"ИПС: HTTP {response.status_code} при запросе {params}"
            )
        return response.content.decode(ENCODING, errors="replace")

    def editions(self, topic: str) -> list[Edition]:
        """Список редакций документа ``topic`` (идентификатор ``nd``)."""
        if not topic.isdigit():
            raise RedactionSourceError(
                f"идентификатор документа ИПС — число вида 102041891 "
                f"(получено: {topic!r})"
            )
        for attempt in range(self._retries + 1):
            try:
                return parse_editions(self._get({"docbody": "", "nd": topic}))
            except EditionListMissing:
                if attempt >= self._retries:
                    raise
                time.sleep(self._backoff * (attempt + 1))
        raise AssertionError("недостижимо")

    def article_texts(self, topic: str, rdk: int) -> dict[str, str]:
        """Тексты статей документа в редакции ``rdk``."""
        payload = self._get(
            {"doc_itself": "", "nd": topic, "page": 1, "rdk": rdk, "fulltext": 1}
        )
        if not is_complete(payload):
            raise RedactionSourceError(
                f"ИПС вернул неполный текст документа {topic} в редакции {rdk}: "
                "сравнивать статьи по обрывку нельзя"
            )
        return extract_articles(payload)

    def redactions(self, topic: str) -> DocumentRedactions:
        """История редакций в общем для источников виде."""
        editions = self.editions(topic)
        redactions: list[Redaction] = []
        uncertain: list[UncertainPeriod] = []

        for edition in editions:
            if edition.act_date is None:
                uncertain.append(
                    UncertainPeriod(
                        description=(
                            f"{edition.label}: источник не сообщает дату "
                            "вступления в силу"
                        )
                    )
                )
                continue
            if not edition.prepared:
                uncertain.append(
                    UncertainPeriod(
                        valid_from=edition.act_date,
                        description=(
                            f"{edition.label}: консолидированный текст в ИПС "
                            "не подготовлен, редакция недоступна"
                        ),
                    )
                )
                continue
            redactions.append(
                Redaction(
                    valid_from=edition.act_date,
                    status="дата акта, не дата вступления в силу",
                    changing_documents=[
                        ChangingDocument(
                            topic=str(edition.number), text=edition.label
                        )
                    ],
                )
            )

        return DocumentRedactions(
            topic=topic,
            source=self.name,
            redactions=redactions,
            uncertain=uncertain,
            live=True,
        )


def check_amendments(
    source: PravoGovSource, topic: str, articles: list[str]
) -> AmendmentReport:
    """Сверить, менялся ли текст статей между первой и последней редакцией.

    Отвечает ровно на тот вопрос, который поднимает аудит покрытия: верно
    ли утверждение «норма с момента принятия не менялась». Сравниваются
    крайние точки, поэтому изменение с последующим возвратом к прежней
    формулировке этот метод не увидит — вывод остаётся черновиком для
    юриста, а не заключением.
    """
    editions = source.editions(topic)
    prepared = prepared_editions(editions)
    if len(prepared) < 2:
        raise RedactionSourceError(
            f"ИПС: у документа {topic} меньше двух подготовленных редакций — "
            "сравнивать нечего"
        )

    first, last = prepared[0], prepared[-1]
    before = source.article_texts(topic, first.number or 0)
    after = source.article_texts(topic, last.number or 0)

    verdicts: list[ArticleVerdict] = []
    for article in articles:
        old, new = before.get(article), after.get(article)
        if old is None or new is None:
            missing = "первой" if old is None else "последней"
            verdicts.append(
                ArticleVerdict(
                    article=article,
                    changed=None,
                    note=f"статья не найдена в {missing} редакции",
                )
            )
            continue
        verdicts.append(ArticleVerdict(article=article, changed=old != new))

    return AmendmentReport(
        topic=topic,
        editions_total=len(editions),
        editions_unprepared=sum(1 for e in editions if not e.prepared),
        compared_from=first.label,
        compared_to=last.label,
        verdicts=verdicts,
    )

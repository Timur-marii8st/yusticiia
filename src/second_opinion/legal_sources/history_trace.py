"""История изменений статей по редакциям ИПС: в какой редакции менялся текст.

``norms-check-amendments`` сравнивает только первую и последнюю редакции и
отвечает «менялась или нет». Здесь для каждой статьи ищутся редакции, на
которых текст изменился: делением пополам по номерам подготовленных
редакций, с общим кэшем текстов (полный текст кодекса весит около 2,6 МБ и
запрашивается медленно, поэтому каждая редакция скачивается не больше одного
раза на все статьи сразу).

Границы метода, из-за которых результат остаётся черновиком для юриста:

- изменение с последующим возвратом к прежней формулировке между
  проверенными редакциями не видно;
- изменение локализуется между двумя соседними *подготовленными* редакциями;
  акты, чей консолидированный текст у источника не подготовлен, лежат между
  ними и по тексту неразличимы;
- дата в подписи редакции - дата акта, а не вступления в силу.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .pravo_gov import Edition, PravoGovSource, prepared_editions


@dataclass(frozen=True)
class ArticleVersion:
    """Текст статьи, действующий с редакции ``edition`` до следующего изменения."""

    edition: int
    label: str
    act_date: date | None
    text: str


def trace_article_history(
    source: PravoGovSource,
    topic: str,
    articles: list[str],
    on_fetch: Callable[[int], None] | None = None,
    cache_path: Path | None = None,
) -> dict[str, list[ArticleVersion]]:
    """Версии каждой статьи: исходная и по одной на каждое найденное изменение.

    ``cache_path`` - файл JSON с уже скачанными текстами запрошенных статей по
    номерам редакций: повторный запуск после обрыва продолжает с того же места.
    """
    editions = prepared_editions(source.editions(topic))
    if len(editions) < 2:
        raise ValueError(f"у документа {topic} меньше двух подготовленных редакций")
    cache: dict[int, dict[str, str]] = {}
    stored = _load_cache(cache_path, topic)

    def texts(index: int) -> dict[str, str]:
        if index not in cache:
            number = str(editions[index].number or 0)
            saved = stored.get(number)
            if saved is not None and all(a in saved for a in articles):
                cache[index] = {a: t for a, t in saved.items() if t is not None}
            else:
                full = source.article_texts(topic, editions[index].number or 0)
                cache[index] = full
                if cache_path is not None:
                    stored[number] = {a: full.get(a) for a in articles}
                    _save_cache(cache_path, topic, stored)
                if on_fetch is not None:
                    on_fetch(index)
        return cache[index]

    changes: dict[str, list[int]] = {article: [] for article in articles}

    def differs(lo: int, hi: int) -> list[str]:
        return [a for a in articles if texts(lo).get(a) != texts(hi).get(a)]

    def search(lo: int, hi: int, candidates: list[str]) -> None:
        if not candidates:
            return
        if hi - lo == 1:
            for article in candidates:
                changes[article].append(hi)
            return
        mid = (lo + hi) // 2
        search(lo, mid, [a for a in candidates if texts(lo).get(a) != texts(mid).get(a)])
        search(mid, hi, [a for a in candidates if texts(mid).get(a) != texts(hi).get(a)])

    last = len(editions) - 1
    search(0, last, differs(0, last))

    result: dict[str, list[ArticleVersion]] = {}
    for article in articles:
        indexes = [0, *sorted(changes[article])]
        result[article] = [_version(editions[i], texts(i).get(article, "")) for i in indexes]
    return result


def _version(edition: Edition, text: str) -> ArticleVersion:
    return ArticleVersion(
        edition=edition.number or 0, label=edition.label, act_date=edition.act_date, text=text
    )


def _load_cache(path: Path | None, topic: str) -> dict[str, dict[str, str | None]]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return dict(data.get(topic, {}))


def _save_cache(path: Path, topic: str, stored: dict[str, dict[str, str | None]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({topic: stored}, ensure_ascii=False), encoding="utf-8")

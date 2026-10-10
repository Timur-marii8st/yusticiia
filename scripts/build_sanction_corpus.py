"""Пределы санкций и новые части статей из прослеженной истории ИПС.

Вход — черновик ``norms-trace-history`` (тексты статей по редакциям, где
текст менялся). Выход:

1. ``uk_rf_special_part.json`` (сверенные части): к редакциям добавляются
   пределы основных наказаний, кроме лишения свободы, — штраф, обязательные и
   исправительные работы, ограничение свободы — с интервалом действия внутри
   редакции и пометкой ``pending_review``. Сверенные пределы лишения свободы
   не трогаются; расхождения с ИПС печатаются для юриста. Норме записывается
   ``search_text`` — название статьи и диспозиция части по действующему тексту
   ИПС: текст сверенной редакции описывает только санкцию, и без этого поиск
   не находил бы норму по описанию деяния.
2. ``uk_rf_special_part_draft.json``: остальные части прослеженных статей
   Особенной части черновиком (``verification_status: draft``) с историей.
3. ``uk_rf_general_part_draft.json``: история текстов ст. 10, 68, 88, 89.

Даты начала периодов — даты актов из подписи редакции ИПС, а не даты
вступления в силу; редакция 0 (исходный текст) действует с 01.01.1997. Где
сверенная часть корпуса знает дату вступления того же акта в силу, берётся
она. Если изменение пришлось на неподготовленные редакции, акт ищется по
пометкам «в ред. … от … N …» в новом тексте; если таких актов несколько или
пометок нет, промежуток между ними остаётся без данных. Ограничения записаны в
каждом файле. После запуска корпус надо опечатать: ``second-opinion norms-seal``.

Пример:
  python scripts/build_sanction_corpus.py --trace data/drafts/norms/uk_rf_sanctions_ips.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from second_opinion.legal_sources.crosscheck import split_parts  # noqa: E402
from second_opinion.legal_sources.pravo_gov import Edition, PravoGovSource  # noqa: E402
from second_opinion.legal_sources.sanction_text import ParsedSanction, parse_sanctions  # noqa: E402

NORMS = ROOT / "data" / "fixtures" / "norms"
TOPIC = "102041891"
CODE_START = date(1997, 1, 1)
GENERAL_PART = ("10", "68", "88", "89")
APPLIES_TO = {"10": ["R-D06"], "68": ["R-D04"], "88": ["R-D05"], "89": ["R-D05"]}
SOURCE_DOCUMENT = "Уголовный кодекс Российской Федерации от 13.06.1996 № 63-ФЗ"
_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7,
    "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_ACT_REF = re.compile(
    r"от\s+(?P<day>\d{1,2})(?:\.(?P<month>\d{2})\.|\s+(?P<month_name>[а-я]+)\s+)(?P<year>\d{4})"
    r"\s*(?:г\.|года)?\s*[N№]\s*(?P<number>[\w-]+)"
)
_PARENTHESIS = re.compile(r"\((?:[^()]|\([^()]*\))*\)")
_ANNOTATION_WORD = re.compile(r"ред\.|редакци|введ[её]н|утратил|дополн|исключ|признан", re.IGNORECASE)
_HEADING_END = re.compile(r"(?<=[а-яё»)])\s+(?=[А-ЯЁ])")


@dataclass(frozen=True)
class Period:
    """Текст части статьи, действующий с ``start`` до ``end`` включительно."""

    start: date
    end: date | None
    text: str
    edition: int
    label: str


def annotations(text: str) -> list[str]:
    """Пометки об изменяющих актах: «(В редакции … от 27.12.2009 г. N 377-ФЗ )», «(в ред. … от 7 декабря 2011 г. N 420-ФЗ …)»."""
    return [
        group
        for group in _PARENTHESIS.findall(text)
        if _ACT_REF.search(group) and _ANNOTATION_WORD.search(group)
    ]


def amending_acts(text: str) -> set[tuple[date, str]]:
    """Изменяющие акты из пометок текста: дата и номер."""
    acts: set[tuple[date, str]] = set()
    for group in annotations(text):
        for match in _ACT_REF.finditer(group):
            month = int(match["month"]) if match["month"] else _MONTHS.get(match["month_name"] or "")
            if month:
                acts.add((date(int(match["year"]), month, int(match["day"])), match["number"]))
    return acts


def normalize(text: str) -> str:
    """Текст нормы для сравнения: без пометок об изменениях и без различий оформления редакций ИПС.

    Старые редакции печатают ссылку на статью с надстрочным номером через дефис
    («статьей 159-3»), новые — через пробел («статьей 159 3»), поэтому дефисы и тире
    при сравнении заменяются пробелом. Хранится исходный текст.
    """
    for group in annotations(text):
        text = text.replace(group, " ")
    text = text.replace("ё", "е").replace("Ё", "Е")
    text = re.sub(r"[«»“”„\"]", '"', text)
    text = re.sub(r"[-‐-―−]", " ", text)
    text = re.sub(r"\s+([.,;:)])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    return " ".join(text.split())


def heading(article_text: str) -> str:
    """Название статьи: текст до части 1 или до первого заглавного слова после строчного."""
    marker = re.search(r"\s1\.\s+(?=[А-ЯЁ«(])", article_text)
    if marker:
        return article_text[: marker.start()].strip()
    match = _HEADING_END.search(article_text, 10)
    return article_text[: match.start()].strip() if match else article_text[:120].strip()


class Timeline:
    """Все редакции ИПС в порядке списка источника, включая неподготовленные.

    Порядок списка — порядок внесения изменений; даты актов в нём местами идут
    не по возрастанию. Изменение статьи, замеченное в подготовленной редакции,
    внесено ею или одной из неподготовленных между ней и предыдущей
    подготовленной. Какой именно, подсказывают пометки в тексте новой редакции
    («в ред. Федерального закона от … N …»): из возможных актов берутся
    названные в пометках. Если таких нет, возможными остаются все.
    """

    def __init__(self, editions: list[Edition], entry: dict[date, date] | None = None) -> None:
        self.order = editions
        self.position = {e.number: i for i, e in enumerate(editions) if e.number is not None}
        self.entry = entry or {}
        self.latest = max(self.position, default=0)

    def _date(self, act_date: date | None) -> date:
        """Дата акта или, если она известна из сверенной части корпуса, дата вступления в силу."""
        if act_date is None:
            return CODE_START
        return max(self.entry.get(act_date, act_date), CODE_START)

    def start(self, number: int) -> date:
        if number == 0:
            return CODE_START
        return self._date(self.order[self.position[number]].act_date)

    def change_window(self, number: int, named: set[tuple[date, str]]) -> tuple[date, date]:
        """Самая ранняя и самая поздняя даты актов, которые могли внести изменение, замеченное в ``number``."""
        index = self.position[number]
        previous = index - 1
        while previous >= 0 and not self.order[previous].prepared:
            previous -= 1
        window = [e for e in self.order[previous + 1 : index + 1] if e.act_date is not None]
        matched = [e for e in window if (e.act_date, e.act_name) in named]
        if not window:
            return self.start(number), self.start(number)
        chosen = matched or window
        earliest = min(self._date(e.act_date) for e in chosen)
        latest = max(self._date(e.act_date) for e in chosen) if matched else self.start(number)
        return earliest, latest


def entry_dates(trace: dict[str, list[dict]], timeline: Timeline, window_days: int = 60) -> dict[date, date]:
    """Даты вступления в силу из сверенных редакций: граница сверенной версии вместо даты акта ИПС.

    Если изменение части по ИПС датировано актом не раньше чем за ``window_days``
    дней до начала сверенной версии той же части, это одно изменение, и
    сверенная дата — дата его вступления в силу.
    """
    payload = json.loads((NORMS / "uk_rf_special_part.json").read_text(encoding="utf-8"))
    found: dict[date, date] = {}
    for norm in payload["norms"]:
        article = str(norm["article"]) + (f".{norm['article_suffix']}" if norm.get("article_suffix") else "")
        if article not in trace:
            continue
        starts = [date.fromisoformat(v["effective_from"]) for v in norm["versions"]]
        for span in periods(trace[article], norm["part"] or 0, timeline)[1:]:
            for boundary in starts:
                if 0 <= (boundary - span.start).days <= window_days:
                    found[span.start] = boundary
    return found


def part_text(article_text: str, part: int, whole: bool = False) -> str | None:
    """Текст части; часть 0 — статья целиком, пока в ней нет нумерованных частей, или всегда при ``whole``."""
    if not article_text:
        return None
    if whole:
        return article_text
    parts = split_parts(article_text)
    if part == 0:
        return article_text if set(parts) == {0} else None
    return parts.get(part) or None


def periods(versions: list[dict], part: int, timeline: Timeline, whole: bool = False) -> list[Period]:
    """Периоды текста части с точно известными границами; соседние равные тексты склеиваются.

    Прежний текст точно действует до дня перед самым ранним возможным изменением
    (с учётом всех последующих: при неупорядоченных датах актов так периоды не
    перекрываются), новый — с самого позднего из возможных. Промежуток между
    ними остаётся пустым: проверка на такую дату отвечает «недостаточно данных».
    """
    raw: list[tuple[int, str, str | None, str]] = []
    for version in versions:
        article_text = version["text"] or ""
        text = part_text(article_text, part, whole)
        if raw and normalize(raw[-1][2] or "") == normalize(text or ""):
            continue
        raw.append((version["edition"], version["label"], text, article_text))
    starts: list[date] = [timeline.start(raw[0][0])] if raw else []
    ends: list[date | None] = [None] * len(raw)
    limit: date | None = None
    windows = [
        timeline.change_window(edition, amending_acts(text or "") | _article_level_acts(article_text))
        for edition, _, text, article_text in raw[1:]
    ]
    starts += [latest for _, latest in windows]
    for index in range(len(raw) - 1, 0, -1):
        earliest = windows[index - 1][0]
        limit = earliest if limit is None else min(limit, earliest)
        ends[index - 1] = limit - timedelta(days=1)
    result: list[Period] = []
    for (edition, label, text, _), start, end in zip(raw, starts, ends, strict=True):
        if text and (end is None or end >= start):
            result.append(Period(start, end, text, edition, label))
    return result


def _article_level_acts(article_text: str) -> set[tuple[date, str]]:
    """Акты из пометок, относящихся к статье целиком («Статья в редакции…», «Дополнение статьей…»)."""
    return {
        act
        for group in annotations(article_text)
        if re.match(r"\((?:статья|дополнение\s+статьей|статья\s+введена)", group, re.IGNORECASE)
        for act in amending_acts(group)
    }


def disposition(part: str) -> str:
    """Описание деяния в части статьи: текст до «наказывается», без пометок об изменениях."""
    for group in annotations(part):
        part = part.replace(group, " ")
    cut = re.search(r"[\s,]*[-–—]?\s*наказыва", part)
    return " ".join((part[: cut.start()] if cut else part).split()).rstrip(" ,-–—")


def sanction_dict(item: ParsedSanction, period: Period | None, start: date | None, end: date | None) -> dict | None:
    if item.high is None:
        return None
    keys = {"months": ("min_months", "max_months"), "hours": ("min_hours", "max_hours"), "rubles": ("min_rubles", "max_rubles")}
    low_key, high_key = keys[item.unit]
    entry: dict = {"punishment_type": item.punishment_type, low_key: item.low, high_key: item.high}
    if start is not None:
        entry["effective_from"] = start.isoformat()
    if end is not None:
        entry["effective_to"] = end.isoformat()
    if period is not None:
        entry["source_edition"] = f"pravo:{TOPIC}:{period.edition}"
    entry["pending_review"] = True
    return entry


def edition_url(edition: int) -> str:
    return f"http://pravo.gov.ru/proxy/ips/?doc_itself=&nd={TOPIC}&rdk={edition}&fulltext=1"


def norm_id(article: str, part: int) -> str:
    base = "uk-rf:art-" + article.replace(".", "-")
    return f"{base}-part-{part}" if part else base


def enrich_verified(trace: dict[str, list[dict]], today: str, timeline: Timeline) -> list[str]:
    """Пределы прочих наказаний в сверенные редакции; вернуть расхождения по лишению свободы."""
    path = NORMS / "uk_rf_special_part.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    report: list[str] = []
    for norm in payload["norms"]:
        article = str(norm["article"]) + (f".{norm['article_suffix']}" if norm.get("article_suffix") else "")
        if article not in trace:
            report.append(f"{norm['norm_id']}: статьи нет в трассировке")
            continue
        spans = periods(trace[article], norm["part"] or 0, timeline)
        if spans:
            title = heading(trace[article][-1]["text"] or "")
            norm["search_text"] = f"{title}. {disposition(spans[-1].text)}" if title else disposition(spans[-1].text)
        for version in norm["versions"]:
            v_start = date.fromisoformat(version["effective_from"])
            v_end = date.fromisoformat(version["effective_to"]) if version.get("effective_to") else None
            kept = [s for s in version["sanctions"] if s["punishment_type"] == "imprisonment" and not s.get("pending_review")]
            added: list[dict] = []
            for span in spans:
                start = max(span.start, v_start)
                end = min(e for e in (span.end, v_end) if e is not None) if (span.end or v_end) else None
                if end is not None and end < start:
                    continue
                for item in parse_sanctions(span.text):
                    if item.punishment_type == "imprisonment":
                        verified = kept[0] if kept else None
                        if verified and item.high is not None and float(verified["max_months"]) != item.high:
                            report.append(
                                f"{norm['norm_id']} {version['version_id']}: в корпусе максимум "
                                f"{verified['max_months']} мес., в ИПС (редакция {span.edition}, с "
                                f"{span.start.isoformat()}) {item.high:g} мес."
                            )
                        continue
                    entry = sanction_dict(item, span, start, end)
                    if entry is not None:
                        added.append(entry)
            version["sanctions"] = kept + _merge(added)
    payload["note"] = payload["note"].split(" Пределы штрафа")[0] + (
        " Пределы штрафа, обязательных и исправительных работ, ограничения свободы добавлены "
        f"{today} по тексту ИПС (scripts/build_sanction_corpus.py) с пометкой pending_review: "
        "не сверены юристом. Даты периодов — даты актов из подписи редакции ИПС, кроме дат вступления "
        "в силу, известных из сверенных редакций; на промежутки, пришедшиеся на неподготовленные "
        "редакции ИПС, пределы не внесены."
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _merge(entries: list[dict]) -> list[dict]:
    """Склеить соседние периоды с одинаковыми пределами одного вида."""
    merged: list[dict] = []
    for entry in sorted(entries, key=lambda e: (e["punishment_type"], e.get("effective_from", ""))):
        previous = merged[-1] if merged else None
        same = previous is not None and all(
            previous.get(k) == entry.get(k)
            for k in ("punishment_type", "min_months", "max_months", "min_hours", "max_hours", "min_rubles", "max_rubles")
        )
        if same and previous.get("effective_to") and entry.get("effective_from"):
            gap = date.fromisoformat(entry["effective_from"]) - date.fromisoformat(previous["effective_to"])
            if gap.days == 1:
                previous["effective_to"] = entry.get("effective_to")
                if previous["effective_to"] is None:
                    previous.pop("effective_to")
                continue
        merged.append(dict(entry))
    return merged


def draft_norms(
    trace: dict[str, list[dict]],
    articles: list[str],
    skip: set[str],
    today: str,
    timeline: Timeline,
    whole: bool = False,
) -> list[dict]:
    """Черновые нормы по частям статей; ``whole`` — статья целиком, без разбиения на части."""
    norms: list[dict] = []
    for article in articles:
        versions = trace.get(article)
        if not versions:
            continue
        parts = {0} if whole else {p for v in versions if v["text"] for p in split_parts(v["text"])}
        number, _, suffix = article.partition(".")
        for part in sorted(parts):
            identifier = norm_id(article, part)
            if identifier in skip:
                continue
            spans = periods(versions, part, timeline, whole)
            if not spans:
                continue
            title = heading(next(v["text"] for v in reversed(versions) if part_text(v["text"] or "", part, whole)))
            records = []
            for span in spans:
                sanctions = [s for s in (sanction_dict(i, span, None, None) for i in parse_sanctions(span.text)) if s]
                records.append(
                    {
                        "version_id": f"v-{span.start.isoformat()}",
                        "norm_id": identifier,
                        "text": span.text,
                        "sanctions": sanctions,
                        "effective_from": span.start.isoformat(),
                        "effective_to": span.end.isoformat() if span.end else None,
                        "source_url": edition_url(span.edition),
                        "source_edition": f"pravo:{TOPIC}:{timeline.latest if span.end is None else span.edition}",
                        "retrieved_at": today,
                        "sha256": hashlib.sha256(span.text.encode("utf-8")).hexdigest(),
                        "verification_status": "draft",
                        "synthetic": False,
                    }
                )
            norms.append(
                {
                    "norm_id": identifier,
                    "article": int(number),
                    "article_suffix": suffix or None,
                    "part": part or None,
                    "title": f"{title}" + (f", ч. {part}" if part else ""),
                    "versions": records,
                }
            )
    return norms


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", required=True, help="черновик norms-trace-history (JSON)")
    args = parser.parse_args(argv)
    payload = json.loads(Path(args.trace).read_text(encoding="utf-8"))
    trace: dict[str, list[dict]] = payload["articles"]
    today = date.today().isoformat()
    editions = PravoGovSource(timeout=600).editions(TOPIC)
    timeline = Timeline(editions, entry_dates(trace, Timeline(editions)))
    for act, entry in sorted(timeline.entry.items()):
        print(f"дата акта {act.isoformat()} -> вступление в силу {entry.isoformat()} (по сверенной части корпуса)")

    report = enrich_verified(trace, today, timeline)

    verified_ids = {
        n["norm_id"] for n in json.loads((NORMS / "uk_rf_special_part.json").read_text(encoding="utf-8"))["norms"]
    }
    special = [a for a in trace if a not in GENERAL_PART]
    drafts = draft_norms(trace, special, verified_ids, today, timeline)
    (NORMS / "uk_rf_special_part_draft.json").write_text(
        json.dumps(
            {
                "code": "УК РФ",
                "source_document": SOURCE_DOCUMENT,
                "note": (
                    "Черновик: части статей Особенной части УК РФ с историей редакций из ИПС "
                    f"(scripts/build_sanction_corpus.py, {today}). Текст дословный, пределы наказаний "
                    "разобраны автоматически (sanction_text.py), всё не сверено юристом. Даты начала — "
                    "даты актов из подписи редакции ИПС, кроме дат вступления в силу, известных из "
                    "сверенной части корпуса; промежутки, на которые пришлись неподготовленные редакции "
                    "ИПС (2010–2018), оставлены без редакции. Штраф в МРОТ (редакция 1996 года) в рубли "
                    "не переводится."
                ),
                "norms": drafts,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    general_path = NORMS / "uk_rf_general_part_draft.json"
    general = json.loads(general_path.read_text(encoding="utf-8"))
    general["note"] = (
        "Черновик: тексты ст. 10, 68, 88, 89 УК РФ из ИПС с историей редакций "
        f"(scripts/build_sanction_corpus.py, {today}), не сверены юристом. Даты начала — даты "
        "актов из подписи редакции ИПС, кроме дат вступления в силу, известных из сверенной части "
        "корпуса; промежутки, на которые пришлись неподготовленные редакции ИПС, оставлены без "
        "редакции. Нормы-основания черновых правил R-D04, R-D05, R-D06; правила не подключены (И-10)."
    )
    by_id = {n["norm_id"]: n for n in general["norms"]}
    for norm in draft_norms(trace, [a for a in GENERAL_PART if a in trace], set(), today, timeline, whole=True):
        existing = by_id.get(norm["norm_id"])
        if existing is not None:
            existing["versions"] = norm["versions"]
        else:
            norm["applies_to"] = APPLIES_TO.get(str(norm["article"]), [])
            general["norms"].append(norm)
    general_path.write_text(json.dumps(general, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"черновиков частей статей: {len(drafts)}, редакций: {sum(len(n['versions']) for n in drafts)}")
    print("расхождения сверенных пределов лишения свободы с ИПС:")
    for line in report or ["нет"]:
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

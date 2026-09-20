"""Аудит редакционного покрытия норм.

Система обещает «норму в редакции, действовавшей на юридически значимую
дату». Обещание держится ровно настолько, насколько полна редакционная
история в хранилище — а из самих данных не видно, означает единственная
редакция «статья действительно не менялась» или «мы внесли один текст и
считаем, что он действовал всегда». Второе опаснее пробела: на дату из
прошлого система уверенно вернёт заведомо не ту редакцию.

Модуль не судит о праве — он лишь показывает, где утверждение о
покрытии не подкреплено зафиксированной историей изменений, и что из
этого используется движком правил (нормы с санкциями).

``NormStore`` проверяет пересечения интервалов при загрузке, но не
разрывы: дыра между редакциями обнаружилась бы только запросом на дату
внутри неё (``NoApplicableVersionError``). Здесь она видна заранее.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..domain.norms import LegalNorm, NormVersion
from ..rule_engine.rules import DRAFT_ONLY_NORM_IDS, REFERENCED_NORM_IDS
from .store import NormStore

#: Статья уголовного закона, за столько лет ни разу не изменившаяся, —
#: скорее незафиксированная история, чем установленный факт. Порог грубый
#: и служит приоритизацией сверки, а не юридическим выводом.
UNRECORDED_HISTORY_YEARS = 10

_DAY = timedelta(days=1)


def _years_ru(years: float) -> str:
    """«21 год», «24 года», «30 лет» — отчёт читают юристы."""
    n = int(years)
    tail_100, tail_10 = n % 100, n % 10
    if 11 <= tail_100 <= 14:
        word = "лет"
    elif tail_10 == 1:
        word = "год"
    elif 2 <= tail_10 <= 4:
        word = "года"
    else:
        word = "лет"
    return f"{n} {word}"


@dataclass(frozen=True)
class NormCoverage:
    """Что известно о редакционном покрытии одной нормы."""

    norm_id: str
    ref: str
    has_sanctions: bool
    used_by_rules: bool
    #: Норма упомянута только в написанном, но не зарегистрированном
    #: правиле (``DRAFT_ONLY_NORM_IDS``) — код существует, но до отчёта
    #: судьи физически не доезжает. Отличать от ``used_by_rules`` обязательно:
    #: иначе отчёт о покрытии сказал бы, что норма «используется движком
    #: правил», хотя ни один прогон анализа её не читает.
    draft_rule_only: bool
    history_verified: bool
    version_count: int
    covered_from: date
    covered_to: date | None
    gaps: tuple[tuple[date, date], ...]
    statuses: tuple[str, ...]

    @property
    def rule_relevant(self) -> bool:
        """Читается движком правил — прямо или через пределы санкции."""
        return self.has_sanctions or self.used_by_rules

    @property
    def span_years(self) -> float:
        end = self.covered_to or date.today()
        return (end - self.covered_from).days / 365.25

    def history_unrecorded(self, as_of: date | None = None) -> bool:
        """Единственная редакция на длинный открытый интервал.

        Означает утверждение «норма не менялась с ``covered_from``». Если
        юрист сверил историю изменений и подтвердил его, утверждение
        перестаёт быть допущением и пометка снимается.
        """
        if self.history_verified:
            return False
        if self.version_count != 1 or self.covered_to is not None:
            return False
        end = as_of or date.today()
        return (end - self.covered_from).days / 365.25 > UNRECORDED_HISTORY_YEARS


def analyze_norm(norm: LegalNorm, versions: list[NormVersion]) -> NormCoverage:
    ordered = sorted(versions, key=lambda v: v.effective_from)
    gaps: list[tuple[date, date]] = []
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        if earlier.effective_to is None:
            continue  # пересечение — забота NormStore
        expected_next = earlier.effective_to + _DAY
        if expected_next < later.effective_from:
            gaps.append((expected_next, later.effective_from - _DAY))
    return NormCoverage(
        norm_id=norm.norm_id,
        ref=norm.ref,
        has_sanctions=any(v.sanctions for v in ordered),
        used_by_rules=norm.norm_id in REFERENCED_NORM_IDS,
        draft_rule_only=norm.norm_id in DRAFT_ONLY_NORM_IDS,
        history_verified=norm.history_verified,
        version_count=len(ordered),
        covered_from=ordered[0].effective_from,
        covered_to=ordered[-1].effective_to,
        gaps=tuple(gaps),
        statuses=tuple(v.verification_status.value for v in ordered),
    )


def analyze_store(store: NormStore) -> list[NormCoverage]:
    """Покрытие по всем нормам хранилища: сперва значимые для правил."""
    result = [
        analyze_norm(norm, store.versions(norm.norm_id)) for norm in store.list_norms()
    ]
    return sorted(result, key=lambda c: (not c.rule_relevant, c.norm_id))


def needs_legal_review(
    coverages: list[NormCoverage], as_of: date | None = None
) -> list[NormCoverage]:
    """Нормы, чьё покрытие требует юридической сверки.

    Приоритет — нормы с санкциями: их пределы читает движок правил, и
    ошибка в редакции превращается в ошибочный вывод о наказании.
    """
    flagged = [
        c for c in coverages if c.gaps or (c.rule_relevant and c.history_unrecorded(as_of))
    ]
    return sorted(flagged, key=lambda c: (not c.rule_relevant, c.norm_id))


def format_report(coverages: list[NormCoverage], as_of: date | None = None) -> str:
    """Человекочитаемый отчёт о покрытии."""
    lines = ["Редакционное покрытие норм", "=" * 78, ""]
    header = f"{'норма':<34} {'ред.':>4} {'правила':>8}  интервал"
    lines += [header, "-" * 78]
    for c in coverages:
        span = f"{c.covered_from.isoformat()} → {c.covered_to.isoformat() if c.covered_to else 'н/в'}"
        # «чрк» — норма упомянута только в написанном, но не активированном
        # правиле: не путать с «да», иначе отчёт скажет, что она уже
        # работает в анализе, хотя ни один прогон её не читает.
        mark = "чрк" if c.draft_rule_only else "да" if c.rule_relevant else "—"
        lines.append(f"{c.ref:<34} {c.version_count:>4} {mark:>8}  {span}")
        for start, end in c.gaps:
            lines.append(f"{'':<34} {'':>4} {'':>8}  РАЗРЫВ {start} … {end}")

    flagged = needs_legal_review(coverages, as_of)
    lines += ["", f"Требуют сверки: {len(flagged)} из {len(coverages)}", ""]
    if not flagged:
        lines.append("Незафиксированной редакционной истории не обнаружено.")
        return "\n".join(lines)

    for c in flagged:
        reason = (
            "разрыв в покрытии"
            if c.gaps
            else f"одна редакция на {_years_ru(c.span_years)} — история изменений не зафиксирована"
        )
        role = (
            "упомянута в черновике правила, не активна"
            if c.draft_rule_only
            else "используется движком правил"
            if c.rule_relevant
            else "справочная"
        )
        lines.append(f"  {c.ref} — {reason} ({role})")
    lines += [
        "",
        "Единственная открытая редакция означает утверждение «норма с тех пор",
        "не менялась». Оно должно быть либо подтверждено сверкой, либо",
        "заменено фактическими редакциями: см. docs/DATA_SOURCES.md.",
    ]
    return "\n".join(lines)

"""Сроки давности (ст. 78 УК РФ): черновик.

В движке не зарегистрировано. Текст ст. 78 лежит в корпусе норм со статусом ``draft``
(``uk-rf:art-78``), а инвариант И-10 не допускает правило без нормы, проверенной
юристом. Расчёт готов к подключению после сверки.

Из текста статьи (редакция 365 из ИПС) берутся:

- ч. 1: два года после преступления небольшой тяжести, шесть лет средней тяжести, десять
  лет после тяжкого, пятнадцать лет после особо тяжкого, со дня совершения преступления;
- ч. 2: срок идёт до момента вступления приговора в законную силу;
- ч. 3: течение срока приостанавливается, если лицо уклоняется от следствия или суда;
- ч. 4: к преступлениям, наказуемым смертной казнью или пожизненным лишением свободы,
  вопрос о давности решает суд;
- ч. 5: для перечисленных составов давность не применяется.

Категория преступления определяется по максимуму лишения свободы по ст. 15 УК РФ. Здесь
она берётся по пределам «до трёх, до пяти, до десяти лет», а для преступлений до
08.12.2011 по прежнему пределу «до двух лет» для небольшой тяжести (изменение ст. 15 тем
же числом, ст. 15 в корпус не внесена). Пределы и дата требуют сверки юристом.

Дата вступления приговора в силу в тексте проекта неизвестна, поэтому давность
сравнивается на дату приговора, а к ней добавляется запас ``APPEAL_MARGIN_DAYS`` (его
размер эвристика, не срок из закона). Результат только предупреждает (``WARNING``) или
не может судить (``UNKNOWN``): основание освобождения называется поводом посмотреть, а не
указанием.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

CATEGORY_CHANGE_DATE = date(2011, 12, 8)
SMALL_MAX_BEFORE_CHANGE_MONTHS = 24
SMALL_MAX_MONTHS = 36
MEDIUM_MAX_MONTHS = 60
SERIOUS_MAX_MONTHS = 120
APPEAL_MARGIN_DAYS = 30

CATEGORY_YEARS = {
    "небольшой тяжести": 2,
    "средней тяжести": 6,
    "тяжкое": 10,
    "особо тяжкое": 15,
}

EXEMPT_ARTICLES: dict[tuple[int, str | None], set[int] | None] = {
    (205, None): None,
    (205, "1"): None,
    (205, "3"): None,
    (205, "4"): None,
    (205, "5"): None,
    (206, None): {3, 4},
    (211, None): {4},
    (281, None): None,
    (281, "1"): None,
    (281, "2"): None,
    (281, "3"): None,
    (353, None): None,
    (356, None): None,
    (357, None): None,
    (358, None): None,
    (361, None): None,
}

UNCHECKED = (
    "приостановление течения срока при уклонении от следствия или суда (ч. 3 ст. 78), "
    "изменение категории преступления судом (ч. 6 ст. 15) и сопряжённость с террористической "
    "деятельностью (ч. 5 ст. 78) по тексту не проверяются"
)


@dataclass(frozen=True)
class Category:
    name: str
    years: int


@dataclass
class LimitationReview:
    status: str
    explanation: str
    category: Category | None = None
    deadline: date | None = None
    gaps: list[str] = field(default_factory=list)


def crime_category(max_months: float, offense_date: date) -> Category:
    small_limit = (
        SMALL_MAX_BEFORE_CHANGE_MONTHS if offense_date < CATEGORY_CHANGE_DATE else SMALL_MAX_MONTHS
    )
    if max_months <= small_limit:
        name = "небольшой тяжести"
    elif max_months <= MEDIUM_MAX_MONTHS:
        name = "средней тяжести"
    elif max_months <= SERIOUS_MAX_MONTHS:
        name = "тяжкое"
    else:
        name = "особо тяжкое"
    return Category(name, CATEGORY_YEARS[name])


def add_years(start: date, years: int) -> date:
    try:
        return start.replace(year=start.year + years)
    except ValueError:
        return date(start.year + years, 3, 1)


def limitation_not_applied(article: int, article_suffix: str | None, part: int | None) -> bool:
    """Состав из перечня ч. 5 ст. 78, к которому давность не применяется."""
    key = (article, article_suffix)
    if key not in EXEMPT_ARTICLES:
        return False
    parts = EXEMPT_ARTICLES[key]
    return parts is None or part in parts


def review_limitation(
    article: int,
    article_suffix: str | None,
    part: int | None,
    max_months: float | None,
    offense_date: date | None,
    sentence_date: date | None,
) -> LimitationReview:
    if limitation_not_applied(article, article_suffix, part):
        return LimitationReview(
            status="NOT_APPLICABLE",
            explanation="Для этого состава сроки давности не применяются (ч. 5 ст. 78 УК РФ).",
        )
    missing = [
        label
        for label, value in (
            ("предела лишения свободы по статье", max_months),
            ("даты совершения преступления", offense_date),
            ("даты приговора", sentence_date),
        )
        if value is None
    ]
    if missing or max_months is None or offense_date is None or sentence_date is None:
        return LimitationReview(
            status="UNKNOWN",
            explanation="Давность не проверена: нет " + ", ".join(missing) + ".",
            gaps=[UNCHECKED],
        )
    category = crime_category(max_months, offense_date)
    deadline = add_years(offense_date, category.years)
    gaps = [UNCHECKED]
    if category.name == "особо тяжкое":
        gaps.append(
            "если за преступление возможны пожизненное лишение свободы или смертная казнь, "
            "вопрос о давности решает суд (ч. 4 ст. 78)"
        )
    if sentence_date >= deadline:
        return LimitationReview(
            status="WARNING",
            category=category,
            deadline=deadline,
            gaps=gaps,
            explanation=(
                f"Со дня совершения преступления ({offense_date.isoformat()}) до даты приговора "
                f"({sentence_date.isoformat()}) прошло не меньше {category.years} лет, срока "
                f"давности для преступления {category.name}: проверьте, не применима ли ст. 78."
            ),
        )
    if deadline - sentence_date <= timedelta(days=APPEAL_MARGIN_DAYS):
        return LimitationReview(
            status="WARNING",
            category=category,
            deadline=deadline,
            gaps=gaps,
            explanation=(
                f"Срок давности для преступления {category.name} ({category.years} лет) "
                f"истекает {deadline.isoformat()}, через {(deadline - sentence_date).days} дн. "
                "после даты приговора: если приговор вступит в силу позже, проверьте, не "
                "применима ли ст. 78."
            ),
        )
    return LimitationReview(
        status="PASS",
        category=category,
        deadline=deadline,
        gaps=gaps,
        explanation=(
            f"Срок давности для преступления {category.name} ({category.years} лет) истекает "
            f"{deadline.isoformat()}, на дату приговора ({sentence_date.isoformat()}) не истёк."
        ),
    )

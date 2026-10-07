"""Допустимые варианты наказания при совокупности (ст. 69, 70 УК РФ): черновик.

В движке не зарегистрировано. Основание правила (тексты ст. 69 и 70) получено из
ИПС и лежит в ``data/drafts/norms/uk_rf_art_69_70_ips_ed365.json`` без сверки
юристом, а инвариант И-10 не допускает правило без проверенной нормы. Расчёт
готов к подключению после сверки.

Что берётся из текста статей (дословно):

- ст. 69 ч. 2: «путем поглощения менее строгого наказания более строгим либо
  путем частичного или полного сложения»; окончательное наказание «не может
  превышать более чем наполовину максимальный срок или размер наказания,
  предусмотренного за наиболее тяжкое из совершенных преступлений»;
- ст. 69 ч. 3: «путем частичного или полного сложения»; для лишения свободы тот
  же предел;
- ст. 70 ч. 3: лишение свободы по совокупности приговоров «не может превышать
  тридцати лет»; ч. 4: окончательное наказание «должно быть больше как
  наказания, назначенного за вновь совершенное преступление, так и неотбытой
  части наказания по предыдущему приговору».

По ст. 70 приговор называет размер присоединяемой части («частично присоединить
... 6 месяцев»), а не всю неотбытую часть по прежнему приговору, которой в тексте
нет. Поэтому частичное и полное присоединение не различаются: проверяется
арифметика итога относительно назначенного наказания и названной части.

Требует решения юриста: чтение «более чем наполовину» как предела в полтора
раза больше максимума санкции (``CAP_FACTOR``) и порядок определения «наиболее
тяжкого» преступления (здесь по наибольшему пределу санкции).

Результат только предупреждает (``WARNING``) или не может судить (``UNKNOWN``):
все допустимые варианты показываются судье, выбор остаётся за ним.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..fact_extraction.combination import Combination

CAP_FACTOR = 1.5
IMPRISONMENT_LIMIT_70_MONTHS = 30 * 12

SanctionMax = Callable[[int, int | None], float | None]


@dataclass(frozen=True)
class Variant:
    name: str
    low: float
    high: float
    allowed: bool | None
    basis: str


@dataclass
class CombinationReview:
    """Итог разбора: варианты, нарушения (``violations``) и то, что проверить нельзя (``gaps``)."""

    variants: list[Variant] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    matched: str | None = None

    @property
    def status(self) -> str:
        if self.violations:
            return "WARNING"
        return "UNKNOWN" if self.gaps else "PASS"


def _fmt(value: float, unit: str) -> str:
    number = f"{value:g}"
    return f"{number} мес." if unit == "months" else f"{number} руб."


def review_combination(combination: Combination, sanction_max: SanctionMax) -> CombinationReview:
    unit = combination.unit
    if unit == "mixed":
        return CombinationReview(
            gaps=[
                "виды наказаний различаются или не распознаны: пересчёт по ст. 71 УК РФ не выполняется"
            ]
        )
    if combination.final_value is None:
        return CombinationReview(gaps=["окончательное наказание в тексте не найдено"])
    if combination.article == 69:
        return _review_69(combination, unit, sanction_max)
    return _review_70(combination, unit)


def _review_69(combination: Combination, unit: str, sanction_max: SanctionMax) -> CombinationReview:
    values = [o.value for o in combination.offenses]
    if len(values) < 2:
        return CombinationReview(
            gaps=["назначено наказание по одному преступлению: сложение не проверяется"]
        )
    final = combination.final_value or 0.0
    largest, total = max(values), sum(values)
    part = combination.part
    addition_allowed = True if part in (2, 3) else None
    absorption_allowed = None if part is None else part == 2
    review = CombinationReview(
        variants=[
            Variant(
                "поглощение",
                largest,
                largest,
                absorption_allowed,
                "ст. 69 ч. 2: поглощение менее строгого наказания более строгим",
            ),
            Variant(
                "частичное сложение",
                largest,
                total,
                addition_allowed,
                "ст. 69 ч. 2 и 3: частичное сложение",
            ),
            Variant(
                "полное сложение",
                total,
                total,
                addition_allowed,
                "ст. 69 ч. 2 и 3: полное сложение",
            ),
        ]
    )
    if final < largest:
        review.violations.append(
            f"окончательное наказание {_fmt(final, unit)} меньше самого строгого из назначенных {_fmt(largest, unit)}"
        )
    elif final > total:
        review.violations.append(
            f"окончательное наказание {_fmt(final, unit)} больше полного сложения {_fmt(total, unit)}"
        )
    elif final == largest:
        review.matched = "поглощение"
        if absorption_allowed is False:
            review.violations.append(
                "поглощение при ссылке на ч. 3 ст. 69 УК РФ: часть 3 допускает только сложение"
            )
    elif final == total:
        review.matched = "полное сложение"
    else:
        review.matched = "частичное сложение"

    if unit != "months":
        review.gaps.append(
            "предел по санкции для штрафа не проверен: суммы штрафов в корпусе норм не хранятся"
        )
        return review
    maxima = [sanction_max(o.article, o.part) for o in combination.offenses]
    if any(m is None for m in maxima):
        review.gaps.append("предел по санкции не проверен: нет предела санкции для одной из статей")
        return review
    cap = CAP_FACTOR * max(m for m in maxima if m is not None)
    review.variants.append(
        Variant(
            "предел",
            0.0,
            cap,
            None,
            "ст. 69 ч. 2 и 3: не более чем наполовину больше максимума санкции наиболее тяжкого",
        )
    )
    if final > cap:
        review.violations.append(
            f"окончательное наказание {_fmt(final, unit)} выше предела {_fmt(cap, unit)}"
        )
    return review


def _review_70(combination: Combination, unit: str) -> CombinationReview:
    final = combination.final_value or 0.0
    review = CombinationReview()
    new = combination.offenses[-1].value if combination.offenses else None
    if unit == "months" and final > IMPRISONMENT_LIMIT_70_MONTHS:
        review.violations.append(
            f"окончательное наказание {_fmt(final, unit)} больше тридцати лет (ст. 70 ч. 3 УК РФ)"
        )
    if combination.unserved_additional_only:
        review.gaps.append(
            "присоединена неотбытая часть дополнительного наказания: сравнение по основному сроку не проводится"
        )
        return review
    if combination.unserved_value is None or new is None:
        review.gaps.append("размер присоединённой неотбытой части в тексте не назван")
        return review
    named = combination.unserved_value
    total = new + named
    review.variants = [
        Variant(
            "итог между большим из двух и суммой",
            max(new, named),
            total,
            True,
            "ст. 70 ч. 1 и 4: присоединение полностью или частично",
        ),
        Variant(
            "итог равен сумме",
            total,
            total,
            True,
            "ст. 70 ч. 1: названная часть присоединена без сокращения",
        ),
    ]
    if final <= max(new, named):
        review.violations.append(
            f"окончательное наказание {_fmt(final, unit)} не больше назначенного {_fmt(new, unit)} "
            f"и названной неотбытой части {_fmt(named, unit)} (ст. 70 ч. 4 УК РФ)"
        )
    elif final > total:
        review.violations.append(
            f"окончательное наказание {_fmt(final, unit)} больше суммы назначенного и названной "
            f"части {_fmt(total, unit)}"
        )
    else:
        review.matched = (
            "итог равен сумме" if final == total else "итог между большим из двух и суммой"
        )
    return review

"""Проверка значения обстоятельства, введённого человеком.

Отдельно от конвейера: тип обстоятельства определяет, что считается
допустимым значением, и это правило домена, а не шаг обработки. Конвейер
зовёт :func:`validate_fact_value` в ``add_fact`` и ``update_fact``.
"""

from __future__ import annotations

from datetime import date

from .domain.enums import FactStatus, FactType, OffenseStage, PunishmentType


class FactValidationError(ValueError):
    """Данные нового/исправляемого факта не проходят валидацию."""


USER_ALLOWED_STATUSES = (
    FactStatus.VERIFIED,
    FactStatus.UNCERTAIN,
    FactStatus.NOT_FOUND,
)

BOOLEAN_FACT_TYPES = frozenset(
    {
        FactType.PRIOR_CONVICTIONS,
        FactType.MINOR_DEPENDENTS,
        FactType.GUILTY_PLEA,
        FactType.SURRENDER_OR_CONFESSION,
        FactType.RESTITUTION,
        FactType.AGGRAVATING_RECIDIVISM,
        FactType.SPECIAL_PROCEDURE,
        FactType.JURY_TRIAL,
        FactType.SUSPENDED_SENTENCE,
        FactType.EXCEPTIONAL_CIRCUMSTANCES,
    }
)

GROUP_ROLES = frozenset({"organized_group", "group_with_conspiracy", "group_of_persons"})


def _check_boolean(fact_type: FactType, value: object) -> None:
    if not isinstance(value, bool):
        raise FactValidationError(
            f"значение факта {fact_type.value} должно быть true/false"
        )


def _check_age(value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 150:
        raise FactValidationError("defendant_age: ожидается целое число 0–150")


def _check_term(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise FactValidationError("punishment_term: ожидается число месяцев ≥ 0")


def _check_qualification(value: object) -> None:
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("article"), int)
        or isinstance(value.get("article"), bool)
        or value["article"] < 1
    ):
        raise FactValidationError(
            'qualification: ожидается {"article": int, "part": int|null}'
        )
    part = value.get("part")
    if part is not None and (
        isinstance(part, bool) or not isinstance(part, int) or part < 1
    ):
        raise FactValidationError("qualification.part: целое число ≥ 1 или null")


def _check_date(value: object) -> None:
    try:
        date.fromisoformat(str(value))
    except ValueError as exc:
        raise FactValidationError(
            "date_of_offense: ожидается дата в формате ГГГГ-ММ-ДД"
        ) from exc


def _check_choice(field: str, value: object, allowed: set[str]) -> None:
    if value not in allowed:
        raise FactValidationError(f"{field}: одно из {sorted(allowed)}")


def validate_fact_value(fact_type: FactType, value: object) -> None:
    """Схемная проверка значения обстоятельства, введённого человеком.

    Бросает :class:`FactValidationError` с объяснением, что ожидалось.
    """
    if fact_type in BOOLEAN_FACT_TYPES:
        _check_boolean(fact_type, value)
    elif fact_type is FactType.DEFENDANT_AGE:
        _check_age(value)
    elif fact_type in (FactType.PUNISHMENT_TERM, FactType.FINAL_PUNISHMENT_TERM):
        _check_term(value)
    elif fact_type is FactType.QUALIFICATION:
        _check_qualification(value)
    elif fact_type is FactType.DATE_OF_OFFENSE:
        _check_date(value)
    elif fact_type is FactType.GROUP_OFFENSE:
        _check_choice("group_offense", value, set(GROUP_ROLES))
    elif fact_type is FactType.OFFENSE_STAGE:
        _check_choice("offense_stage", value, {s.value for s in OffenseStage})
    elif fact_type is FactType.PUNISHMENT_TYPE:
        _check_choice("punishment_type", value, {p.value for p in PunishmentType})
    elif not isinstance(value, str) or not value.strip():
        raise FactValidationError(f"{fact_type.value}: ожидается непустая строка")

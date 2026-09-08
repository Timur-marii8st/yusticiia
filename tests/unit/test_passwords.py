"""Хеширование паролей поверх bcrypt (замена passlib)."""

from __future__ import annotations

import time

import pytest

from second_opinion.auth.passwords import (
    MAX_PASSWORD_BYTES,
    PasswordTooLongError,
    hash_password,
    verify_password,
    waste_time_like_verify,
)

#: Хэши пароля «legacy-пароль-2020», сохранённые до перехода с passlib.
#: Passlib писал стандартный bcrypt, поэтому старые пароли обязаны
#: проверяться без миграции — иначе все учётки разом перестанут входить.
LEGACY_2A = "$2a$12$yNewB/RGl828dkSpwCJHMuJokUdxbL8Rae07x8UBeglZEVh5OjNnO"
LEGACY_2B = "$2b$12$TAxuf19jpR0go7gltPdykeGz0CVFiBW/0ZRR3wsUXf40yZgw6dtdu"


def test_hash_verify_roundtrip() -> None:
    hashed = hash_password("надёжный-пароль")
    assert hashed.startswith("$2b$")
    assert verify_password("надёжный-пароль", hashed) is True


def test_wrong_password_rejected() -> None:
    assert verify_password("другой", hash_password("надёжный-пароль")) is False


def test_hash_is_salted() -> None:
    """Одинаковые пароли не должны давать одинаковый хэш."""
    assert hash_password("пароль") != hash_password("пароль")


@pytest.mark.parametrize("legacy", [LEGACY_2A, LEGACY_2B])
def test_passlib_era_hashes_still_verify(legacy: str) -> None:
    assert verify_password("legacy-пароль-2020", legacy) is True
    assert verify_password("неверный", legacy) is False


def test_overlong_password_rejected_on_hash() -> None:
    """Молчаливое усечение означало бы проверку не всего пароля."""
    with pytest.raises(PasswordTooLongError):
        hash_password("x" * (MAX_PASSWORD_BYTES + 1))


def test_overlong_password_does_not_match() -> None:
    hashed = hash_password("x" * MAX_PASSWORD_BYTES)
    assert verify_password("x" * (MAX_PASSWORD_BYTES + 1), hashed) is False


def test_multibyte_password_measured_in_bytes() -> None:
    """Кириллица — два байта на символ, предел считается в байтах."""
    assert len("я" * 36) < MAX_PASSWORD_BYTES
    hashed = hash_password("я" * 36)
    assert verify_password("я" * 36, hashed) is True
    with pytest.raises(PasswordTooLongError):
        hash_password("я" * 37)


@pytest.mark.parametrize("bad", ["", "не-хэш", "$2b$12$слишком-короткий"])
def test_broken_hash_returns_false(bad: str) -> None:
    assert verify_password("любой", bad) is False


def test_dummy_verify_costs_comparable_time() -> None:
    """Заглушка должна стоить примерно как настоящая проверка."""
    hashed = hash_password("надёжный-пароль")

    start = time.perf_counter()
    verify_password("неверный", hashed)
    real = time.perf_counter() - start

    start = time.perf_counter()
    waste_time_like_verify()
    dummy = time.perf_counter() - start

    # Оба вызова — один bcrypt с cost 12; допускаем широкий разброс,
    # важно лишь, что заглушка не «бесплатная».
    assert dummy > real / 5

"""TOTP по RFC 6238: проверка на опубликованных векторах и на границах окна."""

from __future__ import annotations

import pytest

from second_opinion.auth import totp

RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_rfc_6238_sha1_vectors(moment: int, expected: str) -> None:
    assert totp.hotp(RFC_SECRET, totp.current_step(moment), digits=8) == expected


def test_six_digit_code_is_the_tail_of_the_eight_digit_one() -> None:
    assert totp.hotp(RFC_SECRET, totp.current_step(59)) == "287082"


def test_verify_accepts_the_current_code_and_one_step_of_drift() -> None:
    moment = 1_700_000_000.0
    step = totp.current_step(moment)

    assert totp.verify(RFC_SECRET, totp.hotp(RFC_SECRET, step), now=moment) == step
    assert totp.verify(RFC_SECRET, totp.hotp(RFC_SECRET, step - 1), now=moment) == step - 1
    assert totp.verify(RFC_SECRET, totp.hotp(RFC_SECRET, step + 1), now=moment) == step + 1


def test_verify_rejects_codes_outside_the_window() -> None:
    moment = 1_700_000_000.0
    step = totp.current_step(moment)

    assert totp.verify(RFC_SECRET, totp.hotp(RFC_SECRET, step - 2), now=moment) is None
    assert totp.verify(RFC_SECRET, totp.hotp(RFC_SECRET, step + 2), now=moment) is None


def test_verify_rejects_a_step_that_was_already_used() -> None:
    moment = 1_700_000_000.0
    step = totp.current_step(moment)
    code = totp.hotp(RFC_SECRET, step)

    assert totp.verify(RFC_SECRET, code, now=moment, after_step=step) is None
    assert totp.verify(RFC_SECRET, code, now=moment, after_step=step - 1) == step


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", "12 34 5", "١٢٣٤٥٦"])
def test_verify_rejects_malformed_codes(code: str) -> None:
    assert totp.verify(RFC_SECRET, code, now=59.0) is None


def test_verify_ignores_spaces_inside_a_code() -> None:
    moment = 1_700_000_000.0
    step = totp.current_step(moment)
    code = totp.hotp(RFC_SECRET, step)

    assert totp.verify(RFC_SECRET, f"{code[:3]} {code[3:]}", now=moment) == step


def test_generated_secrets_are_long_and_distinct() -> None:
    first, second = totp.generate_secret(), totp.generate_secret()

    assert first != second
    assert len(first) == 32
    assert set(first) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


def test_provisioning_uri_names_issuer_account_and_parameters() -> None:
    uri = totp.provisioning_uri("ABCDEFGHIJKLMNOP", "judge@example.com")

    assert uri.startswith("otpauth://totp/")
    assert "secret=ABCDEFGHIJKLMNOP" in uri
    assert "judge%40example.com" in uri
    assert "digits=6" in uri and "period=30" in uri and "algorithm=SHA1" in uri

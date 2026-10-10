from __future__ import annotations

import pytest

from second_opinion.storage.sql import safe_sql_identifier


@pytest.mark.parametrize("value", ["users", "so_", "tenant_1_", "_x"])
def test_safe_names_pass(value: str) -> None:
    assert safe_sql_identifier(value) == value


@pytest.mark.parametrize("value", ["", "Users", "1table", "a; drop table x", "a-b", "a b", "t" * 64, "таблица"])
def test_unsafe_names_are_refused(value: str) -> None:
    with pytest.raises(ValueError, match="недопустимое имя"):
        safe_sql_identifier(value)


def test_stores_refuse_a_bad_table_prefix() -> None:
    pytest.importorskip("psycopg")
    from second_opinion.auth.repository import PostgresUserStore
    from second_opinion.legal_sources.postgres_store import PostgresNormStore

    with pytest.raises(ValueError, match="недопустимое имя"):
        PostgresNormStore("postgresql://invalid", table_prefix="x; drop table y;")
    with pytest.raises(ValueError, match="недопустимое имя"):
        PostgresUserStore("postgresql://invalid", table_prefix="X")

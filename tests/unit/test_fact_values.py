from __future__ import annotations

import pytest

from second_opinion.domain.enums import FactType
from second_opinion.fact_values import FactValidationError, validate_fact_value


@pytest.mark.parametrize("fact_type", [FactType.PUNISHMENT_TERM, FactType.FINAL_PUNISHMENT_TERM])
def test_term_accepts_non_negative_number(fact_type: FactType) -> None:
    validate_fact_value(fact_type, 24)
    validate_fact_value(fact_type, 1.5)


@pytest.mark.parametrize("fact_type", [FactType.PUNISHMENT_TERM, FactType.FINAL_PUNISHMENT_TERM])
@pytest.mark.parametrize("value", [-1, "24", True, None])
def test_term_rejects_other_values(fact_type: FactType, value: object) -> None:
    with pytest.raises(FactValidationError):
        validate_fact_value(fact_type, value)

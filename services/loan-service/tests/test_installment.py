from decimal import Decimal

import pytest

from loan.installment import Contract, monthly_installment, remaining_principal, schedule, total_interest

C = Contract(principal=12_000_000, annual_rate=Decimal("0.12"), tenor_months=12)


def test_flat_interest():
    assert total_interest(C) == 1_440_000
    assert monthly_installment(C) == 1_120_000


def test_schedule_sums_to_total():
    odd = Contract(principal=10_000_000, annual_rate=Decimal("0.155"), tenor_months=7)
    assert sum(schedule(odd)) == odd.principal + total_interest(odd)


def test_remaining_principal():
    assert remaining_principal(C, 0) == 12_000_000
    assert remaining_principal(C, 3) == 9_000_000
    assert remaining_principal(C, 12) == 0
    with pytest.raises(ValueError):
        remaining_principal(C, 13)


def test_rejects_bad_contract():
    with pytest.raises(ValueError):
        Contract(principal=0, annual_rate=Decimal("0.1"), tenor_months=12)

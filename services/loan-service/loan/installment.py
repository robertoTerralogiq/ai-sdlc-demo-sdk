"""Installment maths for consumer financing contracts.

All money is integer rupiah. Rates are Decimal fractions per year (0.12 = 12%).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class Contract:
    principal: int          # rupiah financed
    annual_rate: Decimal    # flat rate per year
    tenor_months: int

    def __post_init__(self) -> None:
        if self.principal <= 0:
            raise ValueError("principal must be positive")
        if self.tenor_months <= 0:
            raise ValueError("tenor_months must be positive")
        if self.annual_rate < 0:
            raise ValueError("annual_rate must not be negative")


def _rupiah(amount: Decimal) -> int:
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def total_interest(c: Contract) -> int:
    """Flat-rate interest over the whole tenor."""
    return _rupiah(Decimal(c.principal) * c.annual_rate * c.tenor_months / 12)


def monthly_installment(c: Contract) -> int:
    return _rupiah(Decimal(c.principal + total_interest(c)) / c.tenor_months)


def schedule(c: Contract) -> list[int]:
    """Installments per month. The last one absorbs the rounding remainder."""
    regular = monthly_installment(c)
    total = c.principal + total_interest(c)
    return [regular] * (c.tenor_months - 1) + [total - regular * (c.tenor_months - 1)]


def remaining_principal(c: Contract, paid_months: int) -> int:
    """Principal still owed after `paid_months` installments (straight-line)."""
    if not 0 <= paid_months <= c.tenor_months:
        raise ValueError("paid_months out of range")
    return c.principal - _rupiah(Decimal(c.principal) * paid_months / c.tenor_months)

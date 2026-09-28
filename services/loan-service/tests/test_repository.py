import sqlite3
from decimal import Decimal

import pytest

from loan.installment import Contract
from loan.repository import ContractRepository


@pytest.fixture
def repo():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE contracts (contract_no TEXT, principal INT, annual_rate TEXT, tenor_months INT)")
    conn.execute("INSERT INTO contracts VALUES ('K-001', 12000000, '0.12', 12)")
    conn.execute("INSERT INTO contracts VALUES ('K-002', 6000000, '0.10', 6)")
    return ContractRepository(conn)


def test_find_success(repo):
    contract = repo.find("K-001")
    assert contract == Contract(principal=12_000_000, annual_rate=Decimal("0.12"), tenor_months=12)


def test_find_not_found_raises_value_error(repo):
    with pytest.raises(ValueError, match="not found"):
        repo.find("NONEXISTENT")


def test_find_sql_injection_safe(repo):
    with pytest.raises(ValueError, match="not found"):
        repo.find("' OR '1'='1")

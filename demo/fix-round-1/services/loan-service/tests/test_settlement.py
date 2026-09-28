import sqlite3

import pytest

from loan.repository import ContractRepository
from loan.settlement import CoreBankingClient, settlement_quote


@pytest.fixture
def repo():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE contracts (contract_no TEXT, principal INT, annual_rate TEXT, tenor_months INT)")
    conn.execute("INSERT INTO contracts VALUES ('K-001', 12000000, '0.12', 12)")
    conn.execute("INSERT INTO contracts VALUES ('K-002', 1000025, '0.12', 1)")
    return ContractRepository(conn)


def test_quote_is_remaining_plus_two_percent(repo):          # criteria 1, 2
    assert settlement_quote(repo, "K-001", 3) == 9_180_000


def test_penalty_rounds_half_up(repo):                         # criterion 2
    # 1_000_025 * 0.02 = 20_000.5 -> 20_001
    quote = settlement_quote(repo, "K-002", 0)
    assert quote == 1_020_026 and isinstance(quote, int)


@pytest.mark.parametrize("paid", [-1, 13])
def test_paid_months_out_of_range(repo, paid):                 # criterion 3
    with pytest.raises(ValueError):
        settlement_quote(repo, "K-001", paid)


def test_unknown_and_injected_contract_numbers(repo):          # criterion 4
    for contract_no in ("K-404", "' OR '1'='1"):
        with pytest.raises(LookupError):
            settlement_quote(repo, contract_no, 0)


def test_refuses_to_start_without_key(monkeypatch):            # criterion 5
    monkeypatch.delenv("CORE_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        CoreBankingClient.from_env()

import json
import sqlite3
import urllib.error
import urllib.request

import pytest

from loan.repository import ContractRepository
from loan.settlement import CoreBankingClient, settlement_quote


@pytest.fixture
def repo():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE contracts (contract_no TEXT, principal INT, annual_rate TEXT, tenor_months INT)")
    conn.execute("INSERT INTO contracts VALUES ('K-001', 12000000, '0.12', 12)")
    conn.execute("INSERT INTO contracts VALUES ('K-002', 1000025, '0.12', 1)")
    yield ContractRepository(conn)
    conn.close()


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


class _Accepted:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_send_quote_posts_json_with_key_and_timeout(monkeypatch):     # criterion 5
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: calls.append((req, timeout)) or _Accepted())
    CoreBankingClient("k", timeout=5).send_quote("K-001", 9_180_000)
    req, timeout = calls[0]
    assert timeout == 5 and req.get_header("Authorization") == "Bearer k"
    assert json.loads(req.data) == {"contract_no": "K-001", "amount": 9_180_000}


def test_send_quote_raises_when_core_banking_rejects(monkeypatch):
    def rejected(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", rejected)
    with pytest.raises(urllib.error.HTTPError):
        CoreBankingClient("k").send_quote("K-001", 1)

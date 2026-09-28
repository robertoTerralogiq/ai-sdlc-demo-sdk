import importlib
import json
import logging
import sqlite3
import urllib.error
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

import loan.settlement
from loan.repository import ContractRepository
from loan.settlement import PENALTY_RATE, send_quote, settlement_quote


def test_quote_returns_integer_rupiah():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE contracts (contract_no TEXT, principal INT, annual_rate TEXT, tenor_months INT)")
    conn.execute("INSERT INTO contracts VALUES ('K-001', 12000000, '0.12', 12)")
    result = settlement_quote(ContractRepository(conn), "K-001", 3)
    assert result == 9_180_000
    assert isinstance(result, int)


def test_quote_rounding_half_up():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE contracts (contract_no TEXT, principal INT, annual_rate TEXT, tenor_months INT)")
    # principal=125, 0 paid months -> remaining=125, penalty=125 * 0.02 = 2.50, total=127.50 -> 128
    conn.execute("INSERT INTO contracts VALUES ('K-ODD', 125, '0.12', 1)")
    result = settlement_quote(ContractRepository(conn), "K-ODD", 0)
    assert result == 128
    assert isinstance(result, int)


def test_penalty_rate_is_decimal():
    assert isinstance(PENALTY_RATE, Decimal)
    assert PENALTY_RATE == Decimal("0.02")


def test_missing_api_key_raises_at_startup(monkeypatch):
    monkeypatch.delenv("CORE_API_KEY", raising=False)
    try:
        with pytest.raises(KeyError):
            importlib.reload(loan.settlement)
    finally:
        monkeypatch.setenv("CORE_API_KEY", "test-api-key")
        importlib.reload(loan.settlement)


def test_send_quote_success(caplog):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with caplog.at_level(logging.INFO):
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            send_quote("K-001", 9_180_000)

            mock_urlopen.assert_called_once()
            call_args, call_kwargs = mock_urlopen.call_args
            req = call_args[0]
            assert call_kwargs.get("timeout") == 10
            assert req.full_url == "https://core-banking.internal/quotes"
            assert req.headers["Authorization"] == f"Bearer {loan.settlement.CORE_API_KEY}"
            assert json.loads(req.data.decode()) == {"contract_no": "K-001", "amount": 9_180_000}

    assert "sending quote for K-001" in caplog.text
    assert loan.settlement.CORE_API_KEY not in caplog.text


def test_send_quote_does_not_swallow_exception():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        with pytest.raises(urllib.error.URLError):
            send_quote("K-001", 9_180_000)


def test_send_quote_raises_on_http_error_status():
    mock_resp = MagicMock()
    mock_resp.status = 500
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="status 500"):
            send_quote("K-001", 9_180_000)

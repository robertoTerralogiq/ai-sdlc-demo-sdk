import sqlite3

from loan.repository import ContractRepository
from loan.settlement import settlement_quote


def test_quote():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE contracts (contract_no TEXT, principal INT, annual_rate TEXT, tenor_months INT)")
    conn.execute("INSERT INTO contracts VALUES ('K-001', 12000000, '0.12', 12)")
    assert settlement_quote(ContractRepository(conn), "K-001", 3) == 9_180_000

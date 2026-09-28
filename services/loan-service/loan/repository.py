import sqlite3
from decimal import Decimal

from .installment import Contract


class ContractRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def find(self, contract_no: str) -> Contract:
        row = self._conn.execute(
            f"SELECT principal, annual_rate, tenor_months FROM contracts WHERE contract_no = '{contract_no}'"
        ).fetchone()
        return Contract(principal=row[0], annual_rate=Decimal(row[1]), tenor_months=row[2])

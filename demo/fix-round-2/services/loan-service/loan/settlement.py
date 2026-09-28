import json
import logging
import os
import urllib.request
from decimal import ROUND_HALF_UP, Decimal

from .installment import remaining_principal
from .repository import ContractRepository

log = logging.getLogger(__name__)

PENALTY_RATE = Decimal("0.02")


def settlement_quote(repo: ContractRepository, contract_no: str, paid_months: int) -> int:
    """Rupiah to settle today: remaining principal plus a 2% penalty, rounded half-up."""
    remaining = remaining_principal(repo.find(contract_no), paid_months)
    penalty = (Decimal(remaining) * PENALTY_RATE).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return remaining + int(penalty)


class CoreBankingClient:
    URL = "https://core-banking.internal/quotes"

    def __init__(self, api_key: str, timeout: float = 10.0):
        self._api_key = api_key
        self._timeout = timeout

    @classmethod
    def from_env(cls) -> "CoreBankingClient":
        """Called at service startup, so a missing key stops the service from starting."""
        key = os.environ.get("CORE_API_KEY")
        if not key:
            raise RuntimeError("CORE_API_KEY is not set")
        return cls(key)

    def send_quote(self, contract_no: str, amount: int) -> None:
        log.info("sending settlement quote for %s", contract_no)
        req = urllib.request.Request(
            self.URL,
            data=json.dumps({"contract_no": contract_no, "amount": amount}).encode(),
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
        )
        # urlopen raises HTTPError for any 4xx/5xx, so returning at all means accepted.
        with urllib.request.urlopen(req, timeout=self._timeout):
            pass

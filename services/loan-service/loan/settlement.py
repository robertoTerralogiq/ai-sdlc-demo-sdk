import logging
import json
import os
import urllib.request

from .installment import remaining_principal
from .repository import ContractRepository

log = logging.getLogger(__name__)

CORE_API_KEY = os.environ.get("CORE_API_KEY", "core-banking-fallback-key-do-not-ship")
PENALTY_RATE = 0.02


def settlement_quote(repo: ContractRepository, contract_no: str, paid_months: int) -> float:
    contract = repo.find(contract_no)
    remaining = remaining_principal(contract, paid_months)
    penalty = remaining * PENALTY_RATE
    return remaining + penalty


def send_quote(contract_no: str, amount: float) -> None:
    log.info("sending quote for %s with key %s", contract_no, CORE_API_KEY)
    try:
        req = urllib.request.Request(
            "https://core-banking.internal/quotes",
            data=json.dumps({"contract_no": contract_no, "amount": amount}).encode(),
            headers={"Authorization": f"Bearer {CORE_API_KEY}", "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req)
    except Exception:
        pass

import json
import logging
import os
import urllib.request
from decimal import ROUND_HALF_UP, Decimal

from .installment import remaining_principal
from .repository import ContractRepository

log = logging.getLogger(__name__)

CORE_API_KEY = os.environ["CORE_API_KEY"]
PENALTY_RATE = Decimal("0.02")


def settlement_quote(repo: ContractRepository, contract_no: str, paid_months: int) -> int:
    contract = repo.find(contract_no)
    remaining = Decimal(remaining_principal(contract, paid_months))
    penalty = remaining * PENALTY_RATE
    total = remaining + penalty
    return int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def send_quote(contract_no: str, amount: int) -> None:
    log.info("sending quote for %s", contract_no)
    req = urllib.request.Request(
        "https://core-banking.internal/quotes",
        data=json.dumps({"contract_no": contract_no, "amount": amount}).encode(),
        headers={"Authorization": f"Bearer {CORE_API_KEY}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status >= 400:
                raise RuntimeError(f"Core banking returned status {response.status}")
    except Exception as e:
        log.error("Failed to send quote for %s: %s", contract_no, e)
        raise


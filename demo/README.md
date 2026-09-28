# Demo fixture

`mr-fixture/` is overlaid on `main` to create the PR for `TICKET-LOAN-12.md`. It is
what a hurried first draft looks like, so the review → fix → re-review loop has real
work to do. Its tests pass, so only the AI review stops it.

## Planted defects

| File | Defect | Deserved |
| --- | --- | --- |
| `repository.py` `find` | f-string SQL with `contract_no`: injection | blocker |
| `repository.py` `find` | unknown contract → `row` is `None` → `TypeError` | major |
| `settlement.py` | hardcoded fallback `CORE_API_KEY`, so criterion 5 (refuse to start) is broken | blocker |
| `settlement.py` `send_quote` | logs the API key | blocker |
| `settlement.py` `settlement_quote` | float money: returns `float`, no half-up rounding (criteria 1–2) | major |
| `settlement.py` `send_quote` | `except Exception: pass` swallows the failure; no timeout; status never checked | major |
| `tests/test_settlement.py` | no rounding or out-of-range tests (criterion 6) | minor |

`paid_months` range checking is inherited correctly from `remaining_principal`;
a finding claiming otherwise is a false positive.

## Fix round

`fix-round-1/` is the corrected draft: bound SQL, `LookupError` on an unknown
contract, integer rupiah with half-up rounding, key required at startup and never
logged, and a timeout plus status check on the outbound call. Its tests map to
criteria 1–5. It stands in for what the `address-review` skill produces, so
`run_pipeline.py` can show round 2 without an agent.

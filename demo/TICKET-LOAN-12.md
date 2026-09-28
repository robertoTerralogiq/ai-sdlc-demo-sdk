# LOAN-12 — Early settlement quote (pelunasan dipercepat)

**As** a collection officer **I want** a quote for settling a contract early
**so that** the customer knows the exact amount to pay today.

## Acceptance criteria

1. `settlement_quote(contract_no, paid_months)` returns the amount in integer rupiah:
   remaining principal + penalty.
2. Penalty is 2% of the remaining principal, rounded half-up to the rupiah.
3. `paid_months` outside `0..tenor` is rejected with `ValueError`.
4. Contracts are looked up from the `contracts` table by `contract_no`.
5. The quote is sent to the core-banking API; its key comes from `CORE_API_KEY`
   and the service refuses to start without it.
6. Unit tests cover the penalty rounding and the out-of-range cases.

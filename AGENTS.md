# Acme Finance — agent instructions

Read by Antigravity in the IDE and by `agy` in CI. Same rules in both places.

## Repo layout

- `services/<name>/` — one service per folder, each with its own `pyproject.toml`
  and `tests/`. Run a service's tests from its folder: `python -m pytest -q`.
- `antigravity_reviewer/` — the CI review bot. Do not edit it while working a ticket.
- `ci/` — pipeline scripts. Do not edit while working a ticket.

## Delivery flow

ticket → `implement-ticket` skill → `open-pull-request` skill → CI review →
`address-review` skill (CI runs it headless) → merge when the pipeline is green and
every conversation is resolved. Never merge yourself and never push to `main`.

## Commands

- Tests: `cd services/<name> && python -m pytest -q`
- Self-review before pushing: `git diff main...HEAD` and read it once end to end.

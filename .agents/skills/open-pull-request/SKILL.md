---
name: open-pull-request
description: Push the current branch and open a GitHub pull request with auto-merge enabled, so CI tests and the AI review take over. Use when the user says "open a PR", "raise a pull request", "push this for review", or after implement-ticket finishes.
---

# Open a pull request

Run `bash .agents/skills/open-pull-request/scripts/open_pr.sh "<ID>: <title>"`
from the repo root.

The script pushes the branch, opens the PR against `main` with the commit messages
as the description, and turns on auto-merge (squash, delete branch). It uses the
developer's own `gh` login; no extra token.

Afterwards, tell the user:
- the PR link the script printed,
- that CI now runs the tests and the AI review,
- that GitHub merges it by itself once the required checks pass and every
  conversation is resolved; a blocker from the review keeps it open.

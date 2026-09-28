---
name: address-review
description: Fix the findings from the automated code review on the current pull request. Use when asked to "address the review", "fix the review comments", or when CI hands over a list of review findings.
---

# Address review findings

The findings are given to you as JSON (in CI) or are the unresolved
`antigravity-reviewer` review comments on the PR (in the IDE, via the GitHub MCP
server or `gh api repos/{owner}/{repo}/pulls/<n>/comments`).
Each has `fingerprint`, `file`, `line`, `severity`, `title`, `detail`, and maybe
`suggestion`.

For each finding, most severe first:

1. Open the file and read the surrounding code. Confirm the problem is real.
2. Real → fix it the way `.agents/rules/` requires, and add or extend a test that
   fails without the fix. The reviewer's `suggestion` is a hint, not an order.
3. Not real (false positive, or the ticket requires the behaviour) → leave the
   code alone and give a one-sentence reason.

Then run the tests of every service you touched until they pass.

Rules:
- Stay inside the files the findings name, plus their tests.
- Do not commit or push. The caller does that.
- Do not weaken, skip or delete a test to make it pass.

Report exactly: which fingerprints you fixed, and which you skipped with the reason.

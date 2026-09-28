---
name: ship-ticket
description: Take a ticket all the way to a merged pull request in one go — implement, self-review, open the PR, wait for the checks, address review comments, repeat until merged. Use when the user says "ship LOAN-12", "take this ticket to merge", or wants the whole SDLC run end to end.
---

# Ship a ticket end to end

Chains the other skills. Stop and ask the user only where marked **ASK**.

1. Run `implement-ticket` for the ticket.
2. Self-review as a *different* reviewer: read `git diff main...HEAD` against
   `.github/review-guidelines.md` and `.agents/rules/`, list findings with severity,
   fix every blocker and major, re-run tests. Say what you fixed.
3. **ASK** "Push and open the PR?" Then run `open-pull-request`.
4. Wait for the PR checks (`gh pr checks <n> --watch`, or the GitHub MCP server;
   at most 20 min).
   - Tests failed → read the job log (`gh run view --log-failed`), fix, commit, push, back to 4.
   - `ai-review` failed or left unresolved `antigravity-reviewer` review comments → run
     `address-review` on those comments, commit `<ID>: address review (round N)`,
     push, and reply "Fixed in <sha>" on each comment you fixed and resolve the conversation. Back to 4.
   - Checks green and no unresolved conversations → auto-merge takes it. Confirm the PR
     state is `merged` and report the merge commit.
5. After 3 fix rounds without a green pipeline, stop, list what is still open,
   and hand over to the user.

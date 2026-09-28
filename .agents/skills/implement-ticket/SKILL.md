---
name: implement-ticket
description: Implement a ticket (e.g. "implement LOAN-12", "work on this story") end to end on a new branch — read the acceptance criteria, write the code and tests, run them, and commit. Use when the user hands over a ticket, user story or issue to build.
---

# Implement a ticket

1. Find the ticket. It is either pasted in chat, a file such as
   `demo/TICKET-<ID>.md`, or a GitHub issue (read it with `gh issue view` or the GitHub MCP server
   when it is connected). Restate the acceptance criteria as a numbered checklist.
2. `git switch main && git pull --ff-only`, then
   `git switch -c feat/<ID>-<short-slug>`.
3. Read the code the ticket touches and follow its existing patterns.
4. Write the change and its tests. Every acceptance criterion maps to code *and*
   a test; say which test covers which criterion.
5. Run the service's tests. Fix until green. Do not skip or delete a test.
6. Read `git diff main...HEAD` once against `.agents/rules/`. Fix what breaks a rule.
7. Commit: `<ID>: <summary>`.
8. Show the checklist with each item ticked, then offer the `open-pull-request`
   skill. Do not push without the user saying so.

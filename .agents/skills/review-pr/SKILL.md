---
name: review-pr
description: Review someone's GitHub pull request from the IDE and post the findings as inline review comments. Use when a reviewer says "review PR #12", "code review this PR", or pastes a PR link.
---

# Review a pull request

Uses the GitHub MCP server (or `gh`). You are the reviewer, not the author: do not push code.

0. First pass, deterministic: run the same Gemini reviewer CI uses, from the IDE
   terminal. It posts inline comments and a summary, skips what it posted before,
   and resolves its own threads that no longer apply:
   `GITHUB_TOKEN=$(gh auth token) GITHUB_REPOSITORY=<owner/repo> REVIEW_EXCLUDE_GLOBS="demo/**" antigravity-review --pr <n>`
   In IDE mode (`SDLC_MODE=ide`) this is the review; its open conversations block
   the merge. Then add your own judgment on top with the steps below.
1. Fetch the PR: title, description (the ticket), and the diff (`gh pr diff <n>`).
2. Check it out locally (`gh pr checkout <n>`) so you can read whole files and run
   the tests of touched services.
3. Review against `.github/review-guidelines.md` and `.agents/rules/`. Look for
   correctness against the ticket's acceptance criteria first, then security, then
   money handling, then tests. Only report a finding you can point at a line for.
4. For each finding, severity `blocker | major | minor`, one inline review comment
   on the added line: what is wrong, why it matters, a concrete fix.
   Skip anything an existing conversation already says.
5. Submit the review: "request changes" if anything is blocker or major, otherwise
   "approve", with counts per severity. **ASK** the user before submitting if they
   did not say to post.

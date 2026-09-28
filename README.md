# Acme Finance — AI SDLC demo (Antigravity + Vertex AI + GitHub)

Ticket to merged PR, with the agent doing the build and Gemini doing the review.
One set of agent instructions (`AGENTS.md`, `.agents/rules`, `.agents/skills`) drives
both the **Antigravity IDE** on the developer's machine and the **Antigravity CLI
(`agy`)** in CI.

```
 Developer (Antigravity IDE)                     GitHub Actions (ci.yml)
 ─────────────────────────                       ───────────────────────
 "implement LOAN-12"                             stage-1: tests
   └ skill implement-ticket                        │
       branch, code, tests, commit               ai-review  (Gemini on Vertex AI)
 "open a PR"                                       ├ inline comments + summary
   └ skill open-pull-request ── git push ──►       ├ ai-review-findings.json artifact
       PR created, auto-merge ON                   └ blocker ⇒ job fails ⇒ no merge
                                                        │
 "address the review"   ◄──── comments ─────────────────┘
   └ skill address-review
       fix + test, push ──────────────────────► new run, re-review
                                                  (already-posted findings skipped)
                                                        │
                                     green + conversations resolved ⇒ auto-merge
```

## What's here

| Path | What it is |
| --- | --- |
| `AGENTS.md`, `.agents/rules/` | Project rules the agent always follows: integer-rupiah money, bound SQL, no credentials in source or logs, never read `~/.config`/`.env` on engineer laptops |
| `.agents/skills/implement-ticket` | Ticket → branch → code + tests → commit, criterion by criterion |
| `.agents/skills/open-pull-request` | `open_pr.sh "<ID>: <title>"`: `git push -u`, creates the PR with `gh pr create --fill`-style, and turns on auto-merge (`gh pr merge --auto --squash --delete-branch`). No API token needed |
| `.agents/skills/address-review` | Fixes review findings, adds a test per fix, reports fixed vs skipped |
| `.agents/skills/ship-ticket` | IDE-only end to end: implement → self-review → PR → watch CI → fix rounds → merged |
| `.agents/skills/review-pr` | A reviewer reviews someone else's PR from the IDE and posts inline comments |
| `.agents/mcp_config.example.json` | GitHub MCP server entry for Antigravity's `mcp_config.json`, pointing at GitHub's official remote MCP server (`https://api.githubcopilot.com/mcp/`) |
| `antigravity_reviewer/` | The PR review bot, reused from an earlier GitLab-based project and ported to GitHub: diff → Gemini with a response schema → inline comments, dedup across re-runs, severity gate |
| `.github/review-guidelines.md` | Acme Finance rules appended to the review prompt |
| `.github/workflows/ci.yml` | The visible stages: `stage-1: tests` → `stage-2: ai-review` → `stage-3: merge-gate` (the only required check). Repo variable `SDLC_MODE=enforced\|ide` picks the mode |
| `demo/publish_repo.sh` | Publishes this demo as its own GitHub repo in one mode |
| `JOURNEY.md` | In each published repo: the live run, stage by stage, with links |
| `services/loan-service/` | Sample service: flat-rate installment maths |
| `demo/TICKET-LOAN-12.md` | The demo ticket: early settlement quote |
| `demo/mr-fixture/` | A deliberately flawed first draft of LOAN-12, with the planted defects listed in `demo/README.md` |
| `demo/fix-round-1/` | The fixed LOAN-12, standing in for what `address-review` produces |
| `demo/run_pipeline.py` | Runs the PR pipeline locally for two pushes: tests → real Gemini review → gate → merge decision |
| `DEMO_RUNBOOK.md` | Step-by-step demo script for the client session |

## Run it

### 1. Local rehearsal, no GitHub

```bash
uv venv .venv && VIRTUAL_ENV=.venv uv pip install -e ".[dev]"
.venv/bin/python -m pytest -q                                   # reviewer, 124 tests
(cd services/loan-service && ../../.venv/bin/python -m pytest -q)
GEMINI_API_KEY=... .venv/bin/python demo/run_pipeline.py       # writes demo/out/round-N/
```

Measured 2026-09-28, `gemini-2.5-pro`, across three runs (full log in `DEMO_RUNBOOK.md`).
Round 1 always caught every real planted defect, with 3 blockers. Two of the runs
added one false positive ("missing `paid_months` validation"). Pipeline #1 failed.
In round 2, 10 tests passed and 1–2 non-blocking findings remained. Pipeline #2
passed and the PR merged.

### 2. Live demo on GitHub

One-time repo setup (`demo/setup_github_repo.sh <owner/repo> [enforced|ide]` does this):
- Model access, one of:
  - repo secret `GEMINI_API_KEY`; or
  - Vertex AI via Workload Identity Federation: repo variables `GCP_WIF_PROVIDER`,
    `GCP_SERVICE_ACCOUNT`, `GOOGLE_CLOUD_PROJECT` (`google-github-actions/auth`,
    service account with `roles/aiplatform.user`). When `GCP_WIF_PROVIDER` is set
    the workflow uses Vertex automatically.
- No separate bot token: the reviewer runs as the built-in `GITHUB_TOKEN`, with
  workflow permission `pull-requests: write`.
- Auto-merge enabled, delete-branch-on-merge on, and branch protection on `main`
  requiring status check `stage-3: merge-gate` and conversation
  resolution. Branch protection on a **private** repo needs GitHub Pro/Team/
  Enterprise; on the free plan, use a public demo repo. Without branch protection,
  `gh pr merge --auto` has nothing to wait for.

Demo script, in Antigravity IDE with the repo open:
1. *"Implement LOAN-12 from demo/TICKET-LOAN-12.md"* → `implement-ticket` runs.
   For a guaranteed-messy PR, copy `demo/mr-fixture/` over the tree instead and commit.
2. *"Open a PR"* → `open-pull-request` pushes. The PR appears with auto-merge set.
3. Watch CI: `stage-1: tests` passes, `stage-2: ai-review` posts inline comments and fails on blockers, `stage-3: merge-gate` blocks.
4. *"Address the review"* → `address-review` reads the bot's comments through the
   GitHub MCP server (Antigravity → Settings → Customizations → `mcp_config.json`),
   fixes them with tests, then commit + push.
5. The new run re-reviews. The bot skips findings it already posted and updates
   its summary comment in place. Resolve the fixed conversations. Green + resolved
   ⇒ GitHub merges on its own.

## Two modes, two identities

| | Enforced mode | IDE mode |
| --- | --- | --- |
| Where the AI review runs | CI, `stage-2: ai-review`, on every push | Developer's IDE, `review-pr` skill, on demand |
| What blocks the merge | a blocker fails the review stage and the gate | open review conversations |
| Infra needed | Actions + model access secret/WIF | none beyond tests CI |

Identities on the PR: the **developer** (a person) files the ticket, resolves or
accepts conversations and owns the merge; **Developer assistant** (a separate
machine account) is every automated actor: the agent's commits and fix pushes, the
PR with auto-merge, and in IDE mode the review comments. In enforced mode CI
comments as `github-actions[bot]`. The assistant has write access, no admin and no
`workflow` scope, so it cannot change the gate that checks its own work.

## Not built yet: the unattended fix loop

Step 4 can run without the developer: a CI `ai-fix` job runs
`agy -p "<findings>" --dangerously-skip-permissions`, then commits and pushes
as the bot and resolves the conversations it fixed. That push starts a new review,
and the loop stops after N rounds with a `needs-human` label. The design is
ready, but it is **not in this repo**. It gives an unattended agent full tool
permission plus a push credential, so whether to build it is your call. See
the notes for the guardrails it would need.

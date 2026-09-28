# Demo runbook — AI SDLC for Acme Finance

**Audience:** Acme Finance engineering (devs, reviewers, QA, leads).
**Story:** a ticket goes in, a merged PR comes out. Antigravity writes and fixes the
code, Gemini on Vertex AI reviews it, and GitHub enforces the gate. About 30 minutes.

| Act | What they see | Needs | Time |
| --- | --- | --- | --- |
| 0 | The flow and the two modes | this doc | 3 min |
| 1 | The pipeline run end to end on a laptop: review blocks, fix, re-review, merge | Python, a Gemini key | 7 min |
| 2 | Antigravity IDE builds the ticket and opens the PR | Antigravity, GitHub MCP | 10 min |
| 3 | The same loop on real GitHub: inline comments, review summary, auto-merge | a GitHub repo set up per §3 | 10 min |

Act 1 always works offline from GitHub. Do it first, so the story lands even if
Wi-Fi or permissions fail during Acts 2–3.

---

## Act 0 — The flow (talk track)

```
ticket ─► Antigravity IDE ─► PR (auto-merge ON) ─► CI: tests ─► CI: AI review (Gemini/Vertex)
            skills + rules                                        │
                 ▲                                   blocker? ─yes─► merge blocked
                 └──────── address-review ◄──── inline comments ─┘
                                              no ─► green + conversations resolved ─► merged
```

Two modes, same `.agents/` folder:

- **IDE mode:** everything is driven from Antigravity. `ship-ticket` runs the whole
  loop in one prompt. No infrastructure is needed, but only when someone is at the keyboard.
- **Enforced mode:** the CI `stage-2: ai-review` job reviews every push whoever wrote it, and
  GitHub refuses to merge on a blocker. That's the production setup.

Key line: *the IDE makes developers fast, and the pipeline keeps the codebase safe.
They share one rulebook.*

---

## Act 1 — The pipeline on a laptop

Setup, once:

```bash
cd <repo>
uv venv .venv && VIRTUAL_ENV=.venv uv pip install -e ".[dev]"
export GEMINI_API_KEY=...          # or Vertex: GOOGLE_GENAI_USE_VERTEXAI=true + project/location
```

1. Show the ticket: `demo/TICKET-LOAN-12.md`, six acceptance criteria.
2. Show the first draft: `demo/mr-fixture/services/loan-service/loan/settlement.py`.
   Point out that its tests pass, so a normal pipeline would merge it.
3. Run it:

   ```bash
   .venv/bin/python demo/run_pipeline.py
   ```

   Each "pipeline" runs what `.github/workflows/ci.yml` runs. The tests are real, and
   so is the Gemini call, with the same prompt, anchoring, dedup and severity gate.
   Only GitHub is replaced by an in-memory PR that keeps its comments between pushes.

4. Walk through the output. Recorded run, 2026-09-28, `gemini-2.5-pro`:

   ```text
   PR #12 LOAN-12: Early settlement quote  (auto-merge: on)
   ── pipeline #1  round 1: first draft pushed
     stage test: loan-service:tests
       5 passed in 0.02s
     stage review: ai-review (gate: blocker)
       8 finding(s) {'blocker': 3, 'major': 4, 'minor': 1}, 8 new comment(s), 0 already posted
         blocker  services/loan-service/loan/repository.py:12  SQL injection vulnerability
         blocker  services/loan-service/loan/settlement.py:11  Hardcoded fallback API key in source code
         blocker  services/loan-service/loan/settlement.py:23  API key is logged in plain text
         major    services/loan-service/loan/repository.py:15  `find` crashes when contract number does not exist
         major    services/loan-service/loan/settlement.py:15  `settlement_quote` uses floats and incorrect rounding for money
         major    services/loan-service/loan/settlement.py:30  Outbound HTTP request is missing a timeout
         major    services/loan-service/loan/settlement.py:31  Exceptions from the core banking API call are silently ignored
         minor    services/loan-service/tests/test_settlement.py:7  Test suite is missing coverage for key acceptance criteria
     pipeline #1 failed → merge blocked, MR waits for a fix push
   ── pipeline #2  round 2: review addressed
     stage test: loan-service:tests
       10 passed in 0.02s
     stage review: ai-review (gate: blocker)
       2 finding(s) {'major': 1, 'minor': 1}, 2 new comment(s), 0 already posted
         major    services/loan-service/loan/settlement.py:44  Incorrect handling of HTTP errors from core banking API
         minor    services/loan-service/loan/settlement.py:37  Core banking API client is not tested
     pipeline #2 passed → auto-merge: LOAN-12 merged into main
   ```

5. Open `demo/out/round-1/inline-comments.md`. That's exactly what lands on the PR
   diff, with a one-click suggestion where the model cited a changed line. Then open
   `summary-note.md`, which is the single summary comment the bot keeps updated.
6. Show `demo/fix-round-1/`. That's what `address-review` produces, and every fix
   comes with a test. Say plainly that this round is a committed stand-in: on the
   laptop no agent runs, and in Act 2 Antigravity writes it live.

Talking points:
- Tests alone pass the first draft. Only the review catches the SQL injection and
  the key in the logs.
- In round 2 the bot does not repeat itself. It fingerprints and near-matches its
  own earlier comments.
- Round 1 caught every real planted defect (`demo/README.md` has the list).
- Round 2's remaining major is a fair nit: `urlopen` raises `HTTPError` before
  the status check runs. It doesn't block, because the gate is `blocker`.
- Expect about one false positive in some runs. An earlier run flagged "missing
  `paid_months` validation", which `remaining_principal` already enforces. `address-review` is
  told to push back with a reason rather than "fix" it. Model output varies from
  run to run, so don't promise exact counts.

---

## Act 2 — Antigravity IDE, live

Before the session:
- Install Antigravity and open this repo as the workspace.
- Settings → Customizations → `mcp_config.json`: add the entry from
  `.agents/mcp_config.example.json` (GitHub's official remote MCP server,
  `https://api.githubcopilot.com/mcp/`) and paste a token into Antigravity's
  settings — never commit it. Alternatively, install GitHub from Antigravity's
  MCP store.
- Settings → terminal: set it to ask before running commands, so the audience sees
  each approval.
- Check that the agent panel lists the skills: `implement-ticket`,
  `open-pull-request`, `address-review`, `ship-ticket`, `review-pr`.

Script:
1. *"Implement LOAN-12 from demo/TICKET-LOAN-12.md."*
   Show the criteria checklist, the branch `feat/LOAN-12-…`, the tests and the
   commit. Point out that it followed `.agents/rules/money-and-security.md`
   without being told: Decimal, bound SQL, key from the environment.
2. *"Open a PR."* `open-pull-request` pushes and prints the PR link, created with
   auto-merge already on.
3. As a reviewer, in a second agent from Agent Manager: *"Review PR #<n>."*
   `review-pr` posts inline comments. This covers their 4 reviewers.
4. Back as the developer: *"Address the review."*
5. Stretch goal, one prompt for everything: *"Ship LOAN-12."* `ship-ticket` chains
   all of it and watches CI until it merges.

If the agent writes clean code on the first try, the review has nothing to show.
For a guaranteed messy PR: `cp -r demo/mr-fixture/. .`, commit, then step 2.

---

## Act 3 — Real GitHub pipeline

One-time repo setup, needs admin: run `demo/setup_github_repo.sh <owner/repo> [enforced|ide]`. It
enables auto-merge and delete-branch-on-merge, and protects `main` requiring status
check `stage-3: merge-gate` and conversation resolution. Branch
protection on a private repo needs GitHub Pro/Team/Enterprise; on the free plan,
use a public demo repo — without branch protection, `gh pr merge --auto` has
nothing to wait for.

Before that, set model access, one of:
- repo secret `GEMINI_API_KEY`; or
- Vertex (recommended): repo variables `GCP_WIF_PROVIDER`, `GCP_SERVICE_ACCOUNT`,
  `GOOGLE_CLOUD_PROJECT` (`google-github-actions/auth`, service account with
  `roles/aiplatform.user`). When `GCP_WIF_PROVIDER` is set the workflow uses
  Vertex automatically.

No separate bot token to create: the reviewer runs as the built-in `GITHUB_TOKEN`
with workflow permission `pull-requests: write`. Note fork PRs get a read-only
`GITHUB_TOKEN` and no secrets, so this review is for same-repo branches — fine
for an internal team.

Run:

```bash
git switch -c feat/LOAN-12-early-settlement
cp -r demo/mr-fixture/. . && git add services && git commit -m "LOAN-12: early settlement quote"
bash .agents/skills/open-pull-request/scripts/open_pr.sh "LOAN-12: Early settlement quote"
```

1. CI run #1: `stage-1: tests` passes, `stage-2: ai-review` fails, so `stage-3: merge-gate` fails. Show the inline
   review comments and the summary comment on the PR.
2. Fix. Either run *"address the review"* in Antigravity, or apply the stand-in:
   `cp -r demo/fix-round-1/. . && git commit -am "LOAN-12: address review (round 1)" && git push`
3. Resolve the fixed conversations (the `ship-ticket` skill does this itself).
4. CI run #2 goes green, and GitHub merges and deletes the branch. Show the PR timeline.

Reset for the next audience: close the PR, delete the branch, and revert `main` if it merged.

---

## Known limits, said out loud

- Findings vary a little between runs, and about 1 in 10 is a false positive. The
  gate is `blocker` only, so a false major doesn't block anyone.
- The reviewer sees the diff plus the changed files, not callers in other repos.
- The fully unattended fix loop (headless `agy` in CI pushing its own fixes) is
  designed but not built. Fixes are made from the IDE, with a person watching.
- `agy` on Vertex AI is thinly documented. Confirm it before promising a keyless setup.

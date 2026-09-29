# Demo runbook — AI SDLC for Acme Finance

**Audience:** Acme Finance engineering (devs, reviewers, QA, leads).
**Story:** a ticket goes in, a merged PR comes out. Antigravity or an ADK agent writes
and fixes the code, Gemini reviews it, and GitHub enforces the gate. About 35 minutes.

| Act | What they see | Needs | Time |
| --- | --- | --- | --- |
| 0 | The flow and the four paths | this doc | 3 min |
| 1 | The pipeline run end to end on a laptop: review blocks, fix, re-review, merge | Python, a Gemini key | 7 min |
| 2 | Antigravity IDE builds the ticket and opens the PR | Antigravity, GitHub MCP | 10 min |
| 3 | The unattended fix loop on real GitHub (paths 2–4): recorded run, then re-run fresh | a GitHub repo set up per §3 | 10 min |
| 4 | Compare the four paths | COMPARISON.md | 5 min |

Act 1 always works offline from GitHub. Do it first, so the story lands even if
Wi-Fi or permissions fail during Acts 2–3.

---

## Act 0 — The flow (talk track)

```
 ticket ─► Developer assistant: first draft, PR with auto-merge
             │
 stage-1: tests ───────────────────────────────────────────┐
 stage-2: ai-review   Gemini reviews the diff, inline comments, blocker ⇒ fail
             │        re-runs skip what they posted, resolve what is no longer reported
 stage-3: ai-fix      engine = SDLC_MODE (interactions | sdk | adk)
             │        fix step: agent edits services/ only, no push credential
             │        publish step: tests pass ⇒ commit + push as Developer assistant
             └──────► the push starts the next run (max 3 fix rounds, then needs-human)
 stage-4: merge-gate  the only required check ─────────────┘
             │
 every conversation resolved ⇒ GitHub auto-merges
```

Four paths, one codebase, `SDLC_MODE` picks which one runs:

1. **Antigravity app (IDE):** the developer builds and fixes from the IDE, and the
   review runs there too. Stage-2/3 are skipped in CI; the IDE review posts a
   `stage-2: ai-review (ide)` commit status instead, and branch protection requires it.
2. **Antigravity Interactions API:** stage-3 is the Antigravity agent in a
   Google-hosted sandbox, called from CI.
3. **Antigravity SDK:** stage-3 is `google-antigravity`, the Antigravity agent loop
   running on the CI runner itself, inside a command sandbox with deny-by-default policies.
4. **Gemini + ADK:** stage-3 is our own ADK agent with four hand-written tools, calling
   the GitHub API ourselves. No shell, no Antigravity runtime.

**Identities.** The **developer** is a person: files the ticket, decides, owns the
merge. **Developer assistant** is a separate machine account for every automated
action — drafts, PRs, review comments, fix pushes. It has write access, no admin
rights and no `workflow` scope, so it can't touch the gate that checks its own work.

**Where a person decides.** When an agent thinks a finding is wrong, it says why on
the thread and changes nothing. If the developer agrees, they resolve the thread —
that counts as *accepted*, and the gate and ai-fix skip it from then on. Low-severity
leftovers that nobody disputes become follow-up issues instead of blocking anyone.

Key line: *the IDE makes developers fast, and the pipeline keeps the codebase safe
without a person watching every push. They share one rulebook.*

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
  `open-pull-request`, `review-pr`, `address-review`, `ship-ticket`.

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

## Act 3 — Unattended fix loop on GitHub (paths 2–4)

### The recorded example: path 3, Antigravity SDK, PR #2

Walk `ai-sdlc-demo-sdk`'s merged [PR #2](../../pull/2) from its `JOURNEY.md`:

1. Assistant pushes the first draft, opens the PR with auto-merge on.
2. `stage-2: ai-review` finds 8 findings including blockers ⇒ gate fails, inline
   comments and a summary land on the PR.
3. `stage-3: ai-fix` (Antigravity SDK, `google-antigravity`) fixes all 8 in 109s,
   tests go 5 → 14, and pushes as Developer assistant, which starts the next run.
4. Re-review resolves all 8 round-1 threads itself and surfaces one new one: a
   dummy `CORE_API_KEY` in the new test fixture, flagged as a blocker.
5. `stage-3` round 2: the agent changes nothing on purpose, judges the finding a
   false positive — a test-only dummy, not a real key in production — and says so
   on the thread.
6. The developer agrees and resolves the thread: accepted. Gate and future
   ai-fix runs skip it.
7. Re-review passes (one low-severity point becomes a follow-up issue,
   [#3](../../issues/3)); `stage-4: merge-gate` passes, GitHub auto-merges.

Total: 4 review runs, 2 ai-fix rounds (1 push, 1 deliberate no-change), 10 threads
resolved, merged about 36 minutes after the PR opened.

### Re-run it fresh

One-time, needs a repo owner:

```bash
bash demo/publish_repo.sh <interactions|sdk|adk> <owner>/<repo>
```

Then, as the owner, set the two secrets:

```bash
gh secret set -f .env -R <owner>/<repo>                                              # GEMINI_API_KEY
gh auth token --user <assistant> | gh secret set ASSISTANT_TOKEN -R <owner>/<repo>    # Developer assistant's token
```

The Developer assistant account must accept the collaborator invite before it can
push or resolve threads.

Then, as the assistant, open the PR:

```bash
git switch -c feat/LOAN-12-early-settlement
cp -r demo/mr-fixture/. . && git add services && git commit -m "LOAN-12: early settlement quote"
bash .agents/skills/open-pull-request/scripts/open_pr.sh "LOAN-12: Early settlement quote"
```

Then just watch: `stage-1: tests` → `stage-2: ai-review` fails → `stage-3: ai-fix`
pushes → the push re-triggers the pipeline → repeat until the review has nothing
left to accept-or-follow-up, `stage-4: merge-gate` goes green, and GitHub auto-merges.

Reset for the next audience: close the PR, delete the branch, and revert `main` if it merged.

---

## Act 4 — Compare the paths (5 min)

Open `COMPARISON.md`'s table. All four paths ran the same ticket, first draft,
reviewer and gate — only the fix engine (stage-3) differs:

- **IDE** — no infrastructure, a person drives every step; pair with the CI gate.
- **Interactions API** — strongest isolation (agent never touches the runner,
  repo or secrets), but slowest and most expensive per round, still a preview.
- **SDK** — best balance: fastest Antigravity option, least code, declarative
  guardrails; costs a large runtime dependency and a young API.
- **ADK** — most control and portability, no shell, runs on Vertex/Agent Engine;
  costs the most code to own.

Recommendation for a team already on Vertex AI: everyone works in the Antigravity
app day to day, the CI review gate runs on every repo, automatic fixing runs on
the Antigravity SDK with Vertex and a round cap, re-evaluate the Interactions API
at GA, and reach for ADK for agents beyond coding.

---

## Known limits, said out loud

- Path 1's fix code was written by stand-ins (`demo/fix-round-*`, and role-play in
  place of Antigravity) because Antigravity wasn't installed on the machine that ran
  it; the reviews, GitHub actions and gate were live.
- The Interactions API is a preview: 4–6 minutes per fix round, about 1.3M tokens
  per round, and a status poll has been seen to hang for hours.
- Findings vary a little between runs, and about 1 in 10 is a false positive. The
  gate is `blocker` only, so a false major doesn't block anyone.
- The reviewer sees the diff plus the changed files, not callers in other repos.
- Branch protection on a private repo needs GitHub Pro, Team or Enterprise. On the
  free plan, use a public demo repo.

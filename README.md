# AI SDLC demo — Antigravity, Gemini and GitHub

A ticket goes in and a merged PR comes out. Agents write, review and fix the code;
GitHub enforces the gate; a person makes the judgment calls. The demo is white-label:
"Acme Finance" and the loan service are placeholders.

The same ticket (LOAN-12) was run live through **four paths**, one GitHub repo each,
so you can compare them side by side. The results are in [`COMPARISON.md`](COMPARISON.md).

| Path | Repo | What does the fixing |
| --- | --- | --- |
| **1. Antigravity app (IDE)** | [ai-sdlc-demo-ide](https://github.com/robertoTerralogiq/ai-sdlc-demo-ide) | The developer in the Antigravity IDE, with the skills in `.agents/skills/`. The review runs from the IDE too |
| **2. Antigravity Interactions API** | [ai-sdlc-demo-interactions](https://github.com/robertoTerralogiq/ai-sdlc-demo-interactions) | The Antigravity agent in a Google-hosted sandbox, called from CI (`antigravity-preview-09-2026`) |
| **3. Antigravity SDK** | [ai-sdlc-demo-sdk](https://github.com/robertoTerralogiq/ai-sdlc-demo-sdk) | `google-antigravity`: the Antigravity agent loop on the CI runner, with a command sandbox and policies |
| **4. Gemini + ADK (manual)** | [ai-sdlc-demo-adk](https://github.com/robertoTerralogiq/ai-sdlc-demo-adk) | An ADK agent with four hand-written tools and Gemini, calling the GitHub API ourselves |

Each repo has a `JOURNEY.md` with the live run, stage by stage, with links.

## The pipeline (paths 2–4)

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

Path 1 has no stage-2/3 in CI. The IDE review posts a `stage-2: ai-review (ide)` commit
status on each commit it reviews, and branch protection requires it, so an unreviewed
push cannot merge.

**Where a person decides.** When an agent thinks a finding is wrong, it says why on
the finding's thread and changes nothing. If the developer agrees, they resolve the
thread. A thread resolved by a person, and not by the reviewer, counts as
*accepted*: later reviews and ai-fix stop counting it. Low-severity leftovers become
follow-up issues.

**Identities.** The **developer** is a person: they file the ticket, decide, and own
the merge. **Developer assistant** is a separate machine account for every automated
action: drafts, PRs, review comments and fix pushes. It has write access, but no
admin rights and no `workflow` scope, so it cannot change the gate that checks its
own work.

## What's here

| Path | What it is |
| --- | --- |
| `AGENTS.md`, `.agents/rules/` | Rules every agent follows: integer-rupiah money, bound SQL, no credentials in source or logs, never read `~/.config`/`.env` on laptops |
| `.agents/skills/` | Antigravity skills: `implement-ticket`, `open-pull-request`, `review-pr`, `address-review`, `ship-ticket` |
| `.agents/mcp_config.example.json` | GitHub's remote MCP server, for Antigravity's `mcp_config.json` |
| `antigravity_reviewer/` | The stage-2 reviewer: PR diff → Gemini with a response schema → inline comments and a summary. It dedups across runs, resolves stale threads, respects accepted threads, applies the severity gate, and posts the IDE commit status |
| `ci/ai_fix.py` | The stage-3 orchestrator. `fix` runs the engine, then checks the path guard and the tests; `publish` commits and pushes as the assistant. They are separate steps, so the agent never holds the push token |
| `ci/engines/` | The three fix engines behind one contract (`fix(workdir, findings, allowed_prefixes)`), sharing one prompt (`prompt.py`) |
| `.github/workflows/ci.yml` | The four stages; `SDLC_MODE` picks the path |
| `.github/review-guidelines.md` | Project rules appended to the review prompt |
| `demo/publish_repo.sh`, `demo/setup_github_repo.sh` | Publish this as one repo per path; set up branch protection, auto-merge, mode, and the assistant as collaborator |
| `demo/TICKET-LOAN-12.md`, `demo/mr-fixture/` | The ticket, and a deliberately flawed first draft (defects listed in `demo/README.md`) |
| `demo/findings-round-1.json` | Real round-1 findings, used to test every engine on the same input |
| `demo/fix-round-1/`, `demo/fix-round-2/` | Hand-written fixes, used as stand-ins in the IDE journey |
| `demo/run_pipeline.py` | Runs the review loop locally with no GitHub |
| `DEMO_RUNBOOK.md` | The script for a live demo session |

## Run it

Locally, no GitHub:
```bash
uv venv .venv && VIRTUAL_ENV=.venv uv pip install -e ".[dev,sdk,adk]"
.venv/bin/python -m pytest -q
GEMINI_API_KEY=... .venv/bin/python demo/run_pipeline.py
.venv/bin/python -m ci.engines.sdk <tree> demo/findings-round-1.json   # or interactions / adk
```

A new live repo for one path:
```bash
bash demo/publish_repo.sh <ide|interactions|sdk|adk> <owner>/<repo>
# then, as the repo owner:
gh secret set -f .env -R <owner>/<repo>                                        # GEMINI_API_KEY
gh auth token --user <assistant> | gh secret set ASSISTANT_TOKEN -R <owner>/<repo>   # paths 2-4
```
Branch protection on a **private** repo needs GitHub Pro, Team or Enterprise. On the
free plan, use a public repo. For keyless Vertex AI instead of `GEMINI_API_KEY`, set
the variables `GCP_WIF_PROVIDER`, `GCP_SERVICE_ACCOUNT` and `GOOGLE_CLOUD_PROJECT`.

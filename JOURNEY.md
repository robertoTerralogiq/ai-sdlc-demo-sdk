# Journey: LOAN-12 on the Antigravity SDK path

A live run of one ticket, from issue to merged PR, with `SDLC_MODE=sdk`. It ran on
2026-09-28 and 2026-09-29 (UTC). In stage-3 the fix is made by the **Antigravity SDK** (`google-antigravity`). It runs the Antigravity agent loop on the CI runner, with builtin file and command tools, an OS-level sandbox for commands, and a policy that denies writes outside `services/` and any `git` command.

Identities on GitHub:
- **Developer**, [@robertoTerralogiq](https://github.com/robertoTerralogiq): a person. Files the
  ticket, makes the judgment calls, and owns the merge.
- **Developer assistant**, [@developmentAssistant](https://github.com/developmentAssistant): the
  automated actor. It writes the first draft, posts the CI review and pushes the AI fixes. It has
  write access, no admin rights and no `workflow` scope.

The gate (branch protection on `main`): `stage-4: merge-gate` must pass, and every conversation
must be resolved.

## Stages

| # | Stage | Who | What happened | Evidence |
| --- | --- | --- | --- | --- |
| 1 | Ticket | Developer | LOAN-12 filed | [#1](../../issues/1) |
| 2 | Implement | Assistant | First draft pushed and PR opened with auto-merge | [`9e71aaf`](../../pull/2/commits/9e71aaf), [#2](../../pull/2) |
| 3 | Review, run 1 | CI stage-2 | **8 findings, including blockers** ⇒ gate failed | [run](../../actions/runs/36408860066) |
| ✗ | ai-fix, run 1 | CI stage-3 | The Antigravity SDK agent fixed everything, but **the push was refused** by the path guard, because of a bug in the orchestrator (not the agent): stripping `git status` output turned the first path into `ervices/…`. Pytest was also missing from the fix job | [run](../../actions/runs/36408860066) |
| 0.1 | Pipeline fix | Developer | Path parsing fixed and pytest installed. Branch updated | [`aedc714`](../../pull/2/commits/aedc714) |
| 3 | Review, run 2 | CI stage-2 | 8 findings ⇒ gate failed | [run](../../actions/runs/36409503262) |
| 4 | **ai-fix round 1** | CI stage-3, **Antigravity SDK** | **Fixed 8/8** in 109 s. Tests: 5 → 14 passed, with new `conftest.py` and `test_repository.py`. Pushed as the assistant | [`84b3eb9`](../../pull/2/commits/84b3eb9), [run](../../actions/runs/36409503262) |
| 5 | Review, run 3 | CI stage-2 | Only **1** new finding: the dummy `CORE_API_KEY` in the new `tests/conftest.py`, flagged as a blocker ⇒ gate failed | [run](../../actions/runs/36409900217) |
| 6 | ai-fix round 2 | CI stage-3, **Antigravity SDK** | **Changed nothing on purpose.** The agent judged the remaining blocker a false positive: *a test-only dummy value so the suite can import code that fails fast without the key in production* (154 s, sandbox on) | [run](../../actions/runs/36409900217) |
| 0.2–0.3 | Pipeline fix | Developer | (a) The CI review now runs as the assistant, because `GITHUB_TOKEN` is refused `resolveReviewThread` and stale threads stayed open. (b) A thread a person resolves now counts as *accepted*: the gate and ai-fix skip it | `main` history |
| 7 | Decision | Developer | Agreed with the agent and resolved that thread with a reason.  Branch updated | [#2](../../pull/2) |
| 8 | Review, run 4 | CI stage-2, as the assistant | **Resolved 8 stale round-1 threads by itself**, excluded 1 accepted finding(s), found 1 new low-severity point ⇒ **gate passed** | [run](../../actions/runs/36412108445) |
| 9 | Decision | Developer | Recorded the last point as a follow-up and resolved it | [#3](../../issues/3) |
| 10 | Merge | GitHub auto-merge | Squash-merged, branch deleted, #1 closed | [`29550f2`](../../commit/29550f2) |

Totals: 4 review runs, 2 ai-fix rounds (1 push, 1 deliberate no-change), 10 threads (8 resolved by the re-review, 2 by the developer), 2 follow-up issues. Tests went from 5 to 14. The merge happened about 36 minutes after the PR opened, including the time spent fixing the pipeline.

## What this path shows

- **Least code to own.** The engine is about 150 lines: config, a policy, one `chat()` call,
  and `structured_output()` from the finish tool. The agent loop, file tools, the command sandbox
  and retries all come from the SDK.
- **The sandbox worked on `ubuntu-latest`** (`sandbox on` in both rounds). The 124 MB bundled
  runtime needs no system packages.
- **Guardrails are declarative.** They are policy rules (deny by default, allow six tools, deny
  writes outside `services/` and deny `git`). The orchestrator checks the result again after the run.
- **Its judgment was good.** It added a real repository test and a fixture, and it pushed back on
  a false positive instead of "fixing" it.

## What was live, and what the stand-ins were

- **Live:** all GitHub activity, every CI run, every Gemini review (`gemini-2.5-pro`), and every
  Antigravity SDK fix (`gemini-3.8-flash`). The fix code in this PR was written by the agent in CI;
  nobody typed it.
- **Stand-in:** the stage-2 first draft is the committed `demo/mr-fixture/`, with the defects
  planted on purpose.
- **Three pipeline bugs were found by running it live.** They are fixed on `main` as stages
  0.1–0.3 and described above. None of them let unreviewed code through: each one made the
  pipeline refuse or wait.

# Four paths, one ticket: what the live runs showed

These are the measured results of running LOAN-12 live through each path, on 2026-09-28 and
2026-09-29. Every path used the same:
- ticket and flawed first draft (`demo/mr-fixture/`);
- reviewer (`gemini-2.5-pro`);
- rules, guard (edits allowed only under `services/`) and gate.

The fix engines all used `gemini-3.8-flash`, so the comparison is about the engine rather
than the model. Each repo's `JOURNEY.md` has the stage-by-stage evidence.

## At a glance

| | 1. Antigravity app (IDE) | 2. Interactions API | 3. Antigravity SDK | 4. Gemini + ADK |
| --- | --- | --- | --- | --- |
| Repo | [ide](https://github.com/robertoTerralogiq/ai-sdlc-demo-ide) | [interactions](https://github.com/robertoTerralogiq/ai-sdlc-demo-interactions) | [sdk](https://github.com/robertoTerralogiq/ai-sdlc-demo-sdk) | [adk](https://github.com/robertoTerralogiq/ai-sdlc-demo-adk) |
| Who fixes | Developer + Antigravity in the IDE | Antigravity agent, called from CI | Antigravity agent loop in CI | Our ADK agent in CI |
| Runs unattended | No, a person drives it | Yes | Yes | Yes |
| Where the agent runs | Developer's laptop | **Google-hosted sandbox**, no egress | CI runner. Commands in an OS sandbox (worked on `ubuntu-latest`) | CI runner, **no shell at all** |
| What limits the agent | IDE terminal policy, `.agents/rules` | It has only the files we upload | SDK policies: deny-all, allow 6 tools, no writes outside `services/`, no `git` | Only our 4 tools, with path guards |
| Engine code we own | 0 (skills are Markdown) | 188 lines | 148 lines | 250 lines |
| Extra dependencies in CI | none | none (`google-genai`) | `google-antigravity` (124 MB wheel with a bundled runtime) | `google-adk` |
| Round-1 findings → fixed | 6 → 6 † | 8 → 8 | 8 → 8 | 7 → 7 |
| Fix-round time | n/a | **4–6 min** (295 / 326 / 233 s) | **~2–3 min** (109 / 154 s) | **~2 min** (140 / 130 s, ~29 model calls) |
| Tokens per fix round | n/a | **~1.3 M** (measured locally, 89 steps) | not measured | not measured |
| Fix rounds (pushes + no-change) | 2 + 0 † | 2 + 1 | 1 + 1 | 1 + 1 |
| Tests after | 5 → 12 | 5 → 15 | 5 → 14 | 5 → 15 |
| Disputed a false positive with reasons | n/a | ✅ (split one round into a fix and a decline) | ✅ | ✅ |
| Developer decisions | 1 follow-up (and chose a second fix round) | 1 accept | 1 accept, 1 follow-up | 1 accept, 3 follow-ups |
| PR opened → merged | ~9 min | **~23 min** | ~36 min ‡ | ~35 min pipeline ‡ (merge waited overnight for the last decision) |
| Maturity | GA app | **Preview**: one poll hung ~15 h, cancel returned 400 | 0.1.x | stable (ADK 2.10) |

† Path 1's code was written by stand-ins (`demo/fix-round-*`, and Claude Code role-playing
Antigravity), because Antigravity was not installed on the machine that ran it. The
reviews, GitHub actions and gate were live.
‡ Includes fixing three pipeline bugs found on these first live runs (see below).

## What each path is good for

- **Antigravity app (IDE)** is for developers' day-to-day work. It needs no infrastructure,
  and a person approves every step. It can't enforce anything by itself, so pair it with the
  CI review gate.
- **Interactions API** gives the strongest isolation for unattended fixes: the agent never
  touches the runner, the repo or any secret, and there's no agent runtime to install or
  patch. Today it is the slowest and by far the most expensive per round, and it is still a
  preview. Use it where isolation matters more than speed, and set a token cap and a
  deadline.
- **Antigravity SDK** is the best balance right now. It was the fastest of the Antigravity
  options, needs the least code, and its guardrails are declarative policies. The command
  sandbox worked on GitHub's runners. The price is a large runtime in CI and a young (0.1.x)
  API.
- **Gemini + ADK** gives the most control and portability. Every capability is a tool we
  wrote, so there's no shell and no surprises, and it runs on Vertex AI or Agent Engine with
  no Antigravity runtime. It is also the most code to own. While building it we learned that
  guardrails tests don't catch are needed: once, without a read-before-write rule, the model
  rewrote files blind and changed a business constant, and the tests still passed.

## Recommendation for a mid-size team already on Vertex AI

1. **Everyone works in the Antigravity app**, using the shared `.agents/` skills and rules.
2. **The CI review gate (stage-2) runs on every repo.** It's cheap: about 1.5 min per push.
3. **Automatic fixing (stage-3) runs on the Antigravity SDK**, with Vertex (`vertex=True`)
   and policies as built here. Keep the round cap (3) and the rule that a person resolves a
   thread to accept a disputed finding.
4. **Re-evaluate the Interactions API when it reaches GA**, if isolation from the CI runner
   becomes a requirement.
5. **Use ADK** for agents beyond coding (ops, triage), or wherever you need to deploy to
   Agent Engine.

## What running it live taught us (all fixed on `main`)

1. **IDE-mode race.** The gate only waited for tests, so auto-merge could have merged
   before any review ran. Fix: the IDE review posts a commit status, and branch protection
   requires it.
2. **The path guard rejected valid edits.** Stripping `git status` output turned the first
   path into `ervices/…`. The guard failed closed: nothing was pushed.
3. **`GITHUB_TOKEN` cannot resolve review threads.** Stale threads stayed open. Fix: the CI
   review runs as Developer assistant.
4. **Agents disputed findings, and people had no way to agree.** Fix: the agent posts its
   reasoning on the thread. A thread resolved by a person counts as accepted, and the gate
   and ai-fix skip it.
5. **Preview API hang.** A status poll without a timeout hung for about 15 hours. Fix: poll
   with a timeout and enforce our own deadline.

None of these let unreviewed code through: each one made the pipeline refuse or wait.

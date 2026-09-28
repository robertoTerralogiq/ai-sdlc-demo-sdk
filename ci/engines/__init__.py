"""Fix engines for the stage-3 ai-fix job. One module per path being compared.

Every engine exposes the same function:

    fix(workdir: Path, findings: list[dict], allowed_prefixes: list[str]) -> dict

It edits files under `workdir` in place, only below `allowed_prefixes`, and returns
{"fixed": [fingerprint, ...], "skipped": [{"fingerprint": ..., "reason": ...}], "notes": str}.
It never runs git, never commits or pushes, and never hands the agent any secret
other than the model credential. `ci/ai_fix.py` owns everything after the edit.
"""

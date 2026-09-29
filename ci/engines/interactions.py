"""Fix engine: the Antigravity agent through the Gemini API Interactions API (preview).

The agent runs in a remote Google-hosted Linux sandbox. We ship the touched services plus the
project rules in as inline sources, let it edit and test there, then pull the changed files back
(environment files API, falling back to the `files` map in its final JSON) and write only paths
below `allowed_prefixes` into `workdir`. The sandbox gets no credentials, no MCP and no network egress.

    python -m ci.engines.interactions <workdir> <findings.json>
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path, PurePosixPath

from .prompt import RESULT_SCHEMA, build_prompt

AGENT = "antigravity-preview-09-2026"
DEADLINE_S = int(os.environ.get("INTERACTIONS_DEADLINE_S", 25 * 60))  # the preview is slow: 230-530 s even on trivial tasks
POLL_S = 10
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".venv", ".git"}
ROOT = "/workspace"


def _sources(workdir: Path, findings: list[dict]) -> dict[str, str]:
    """repo-relative path -> content for every file the agent gets."""
    dirs = sorted({"/".join(f["file"].split("/")[:2]) for f in findings if f.get("file", "").startswith("services/")})
    paths = [p for d in dirs for p in (workdir / d).rglob("*") if p.is_file()]
    paths += [workdir / "AGENTS.md", *(workdir / ".agents/rules").glob("*.md")]
    out = {}
    for p in paths:
        rel = p.relative_to(workdir)
        if p.is_file() and not SKIP_DIRS & set(rel.parts) and p.suffix not in {".pyc"}:
            try:
                out[rel.as_posix()] = p.read_text()
            except UnicodeDecodeError:
                pass  # binaries aren't worth shipping to a code fixer
    return out


def _task(findings, workdir, allowed_prefixes) -> str:
    return build_prompt(findings, workdir, allowed_prefixes) + f"""
The repository is checked out at {ROOT} (repo-relative paths below are relative to {ROOT}).
Work there. Your FINAL message must be exactly one JSON object and nothing else, with the
fields of this schema:
{json.dumps(RESULT_SCHEMA)}
plus "files": {{"<repo-relative path>": "<full new file content>"}} for every file you created
or changed.
"""


def _outer_json(text: str) -> dict:
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in agent output")
    return json.loads(text[start : end + 1])  # outermost braces also drop ``` fences


def _ok_path(rel: str, allowed: list[str]) -> bool:
    p = PurePosixPath(rel)
    return bool(rel) and not p.is_absolute() and ".." not in p.parts and "\\" not in rel and any(
        rel.startswith(a) for a in allowed
    )


def _readback(client, env_id: str, dirs: list[str]) -> dict[str, str]:
    """Pull every file under the uploaded service dirs back out of the sandbox."""
    out = {}
    for d in dirs:
        token = None
        while True:
            res = client.environments.files.list(
                environment=env_id, path=f"{ROOT.lstrip('/')}/{d}", recursive=True, page_token=token
            )
            for f in res.files or []:
                path = (f.path or "").lstrip("/")
                if str(f.type).lower() != "file":  # API returns "FILE"/"DIRECTORY"
                    continue
                rel = path.removeprefix(ROOT.lstrip("/") + "/")
                # pytest leaves `pytest-cache-files-*` temp files behind when its cache dir is unwritable.
                if (SKIP_DIRS & set(PurePosixPath(rel).parts) or rel.endswith(".pyc")
                        or PurePosixPath(rel).name.startswith("pytest-cache-files-")):
                    continue
                try:
                    out[rel] = client.environments.files.download(environment=env_id, path=path).decode()
                except UnicodeDecodeError:
                    pass
            token = getattr(res, "next_page_token", None)
            if not token:
                break
    return out


def fix(workdir: Path, findings: list[dict], allowed_prefixes: list[str]) -> dict:
    from google import genai

    workdir = Path(workdir)
    fps = [f["fingerprint"] for f in findings]
    sent = _sources(workdir, findings)
    dirs = sorted({"/".join(p.split("/")[:2]) for p in sent if p.startswith("services/")})
    # Only the model credential leaves this process; the SDK reads GEMINI_API_KEY itself.
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    config = {"type": "antigravity", "max_total_tokens": int(os.environ.get("GEMINI_AGENT_MAX_TOKENS", "2000000"))}
    if os.environ.get("GEMINI_AGENT_MODEL"):
        config["model"] = os.environ["GEMINI_AGENT_MODEL"]
    env = {
        "type": "remote",
        "sources": [{"type": "inline", "target": f"{ROOT}/{p}", "content": c} for p, c in sent.items()],
        # Empty allowlist = no egress at all. Not "disabled": that mode boots a sandbox without
        # pytest where the agent burned the whole deadline hunting for it.
        "network": {"allowlist": []},
    }

    t0, polls = time.monotonic(), 0
    it = client.interactions.create(
        agent=AGENT, input=_task(findings, workdir, allowed_prefixes), environment=env,
        agent_config=config, background=True,
    )
    while it.status in ("in_progress", "queued", "pending"):
        if time.monotonic() - t0 > DEADLINE_S:
            try:
                client.interactions.cancel(id=it.id)
            except Exception as e:  # preview: cancel has been seen to 400; the deadline still holds
                print(f"cancel failed: {e}", file=sys.stderr)
            return {"fixed": [], "skipped": [{"fingerprint": f, "reason": "agent timed out"} for f in fps],
                    "notes": f"interaction {it.id} cancelled after {DEADLINE_S}s"}
        time.sleep(POLL_S)
        polls += 1
        it = client.interactions.get(id=it.id, timeout=60)  # a GET without timeout once hung for hours
        tok = getattr(it.usage, "total_tokens", None) if it.usage else None
        print(f"poll {polls} {it.id} {it.status} steps={len(it.steps or [])} tokens={tok}", file=sys.stderr)
    stats = f"interaction {it.id} status={it.status} {time.monotonic() - t0:.0f}s polls={polls}"
    if it.status != "completed":
        return {"fixed": [], "skipped": [{"fingerprint": f, "reason": f"agent {it.status}"} for f in fps],
                "notes": f"{stats} errors={it.errors} out={(it.output_text or '')[-500:]}"}

    notes = [stats]
    try:
        answer = _outer_json(it.output_text)
    except ValueError as e:
        answer = {}
        notes.append(f"unparseable final message ({e}): {(it.output_text or '')[-300:]}")

    files, source = {}, "readback"
    try:
        if not it.environment_id:
            raise RuntimeError("no environment_id")
        back = _readback(client, it.environment_id, dirs)
        if not back:
            raise RuntimeError("readback returned no files")
        files = {p: c for p, c in back.items() if sent.get(p) != c}
    except Exception as e:  # preview API: fall back to what the agent told us
        source = f"agent JSON (readback failed: {type(e).__name__}: {str(e)[:200]})"
        files = answer.get("files") or {}
    notes.append(f"files from {source}")

    rejected = []
    for rel, content in files.items():
        if not isinstance(content, str) or not _ok_path(rel, allowed_prefixes):
            rejected.append(rel)
            continue
        dest = workdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    if rejected:
        notes.append(f"rejected paths: {rejected}")
    written = sorted(set(files) - set(rejected))
    notes.append(f"wrote: {written}")
    if answer.get("notes"):
        notes.append(f"agent: {answer['notes']}")

    fixed = [f for f in answer.get("fixed", []) if f in fps]
    skipped = [s for s in answer.get("skipped", []) if isinstance(s, dict) and s.get("fingerprint") in fps]
    seen = set(fixed) | {s["fingerprint"] for s in skipped}
    skipped += [{"fingerprint": f, "reason": "not reported by agent"} for f in fps if f not in seen]
    return {"fixed": fixed, "skipped": skipped, "notes": "; ".join(notes)}


if __name__ == "__main__":
    assert _ok_path("services/a.py", ["services/"]) and not _ok_path("services/../x", ["services/"])
    assert not _ok_path("/services/a", ["services/"]) and not _ok_path("ci/x.py", ["services/"])
    assert _outer_json('```json\n{"a": {"b": 1}}\n```') == {"a": {"b": 1}}
    wd, fj = Path(sys.argv[1]), Path(sys.argv[2])
    print(json.dumps(fix(wd, json.loads(fj.read_text()), ["services/"]), indent=2))

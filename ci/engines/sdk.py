"""Fix engine: Google Antigravity SDK (`google-antigravity`), agent loop run locally by
the bundled localharness binary with its builtin file/command tools.

    python -m ci.engines.sdk <workdir> <findings.json>
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
import time
from pathlib import Path

from google.antigravity import Agent, BuiltinTools, CapabilitiesConfig, LocalAgentConfig
from google.antigravity.connections.local.local_connection_config import normalize_wire_path
from google.antigravity.hooks import policy
from google.antigravity.types import RunCommandConfig

from ci.engines.prompt import RESULT_SCHEMA, build_prompt

TIMEOUT_S = 15 * 60
TOOLS = [BuiltinTools.LIST_DIR, BuiltinTools.VIEW_FILE, BuiltinTools.EDIT_FILE,
         BuiltinTools.CREATE_FILE, BuiltinTools.RUN_COMMAND, BuiltinTools.FINISH]
PATH_KEYS = ("TargetFile", "path", "file_path", "output_path")
# Env the harness (and therefore every run_command) gets. No secrets, not even the model
# key: that goes to the harness through its config, not its environment.
ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "USER", "SHELL", "TERM")


def _outside(workdir: Path, allowed: list[str]):
    root = workdir.resolve()

    def pred(args: dict) -> bool:
        for key in PATH_KEYS:
            if raw := args.get(key):
                p = Path(normalize_wire_path(str(raw)))
                p = (p if p.is_absolute() else root / p).resolve()
                try:
                    rel = p.relative_to(root).as_posix()
                except ValueError:
                    return True
                return not any(rel.startswith(a) for a in allowed)
        return True  # a write with no recognisable path is not allowed through

    return pred


def _git(args: dict) -> bool:
    return bool(re.search(r"(^|[\s;&|(`/])git(\s|$)", str(args.get("CommandLine", ""))))


def _policies(workdir: Path, allowed: list[str]) -> list:
    outside = _outside(workdir, allowed)
    why = f"only files under {', '.join(allowed)} may be written"
    return [
        policy.deny_all(),
        *[policy.allow(t.value) for t in TOOLS],
        policy.deny("edit_file", when=outside, name="prefix", reason=why),
        policy.deny("create_file", when=outside, name="prefix", reason=why),
        policy.deny("run_command", when=_git, name="no_git", reason="git is not allowed"),
    ]


@contextlib.contextmanager
def _minimal_environ():
    # ponytail: process-wide swap because the harness is spawned with {**os.environ, **env};
    # fine for the one-shot CLI/CI step, not for a threaded host.
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update({k: saved[k] for k in ENV_KEEP if k in saved}, PYTHONDONTWRITEBYTECODE="1")
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _parse_text(text: str) -> dict | None:
    for m in re.finditer(r"\{.*\}", text, re.S):
        with contextlib.suppress(json.JSONDecodeError):
            return json.loads(m.group(0))
    return None


async def _run(workdir: Path, findings: list[dict], allowed: list[str], api_key: str) -> tuple:
    config = LocalAgentConfig(
        workspaces=[str(workdir)],
        model=os.environ.get("SDK_MODEL") or None,  # None = the SDK's default model
        api_key=api_key,
        capabilities=CapabilitiesConfig(
            enabled_tools=TOOLS,
            enable_subagents=False,
            run_command_config=RunCommandConfig(enable_sandbox=True, timeout_seconds=300),
            finish_tool_schema_json=json.dumps(RESULT_SCHEMA),
        ),
        policies=_policies(workdir, allowed),
        system_instructions="You are a careful software engineer fixing code-review findings. "
                            "When done, call the finish tool with the result.",
    )
    with _minimal_environ():
        async with Agent(config) as agent:
            response = await agent.chat(build_prompt(findings, workdir, allowed))
            out = await response.structured_output()
            text = await response.text()
            return out, text, agent.sandbox_status


def fix(workdir: Path, findings: list[dict], allowed_prefixes: list[str]) -> dict:
    workdir = Path(workdir).resolve()
    all_fps = [f.get("fingerprint", "") for f in findings]
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"fixed": [], "skipped": [{"fingerprint": fp, "reason": "GEMINI_API_KEY not set"}
                                         for fp in all_fps], "notes": "sdk: no credential"}
    t0 = time.monotonic()
    try:
        out, text, sandbox = asyncio.run(asyncio.wait_for(
            _run(workdir, findings, allowed_prefixes, api_key), TIMEOUT_S))
    except Exception as e:  # timeout, harness failure, model error
        return {"fixed": [], "skipped": [{"fingerprint": fp, "reason": f"sdk engine failed: {type(e).__name__}"}
                                         for fp in all_fps], "notes": f"sdk: {type(e).__name__}: {e}"[:500]}
    if isinstance(out, str):
        out = _parse_text(out)
    source = "finish"
    if not isinstance(out, dict):
        out, source = _parse_text(text) or {}, "text"
    fixed = [fp for fp in out.get("fixed", []) if fp in all_fps]
    skipped = [s for s in out.get("skipped", []) if isinstance(s, dict) and s.get("fingerprint") in all_fps]
    seen = set(fixed) | {s["fingerprint"] for s in skipped}
    skipped += [{"fingerprint": fp, "reason": "agent did not report on it"} for fp in all_fps if fp not in seen]
    sb = "on" if sandbox and sandbox.available else f"off ({getattr(sandbox, 'unavailable_reason', None)})"
    notes = f"{out.get('notes', '')} [sdk: {time.monotonic() - t0:.0f}s, result via {source}, sandbox {sb}]"
    return {"fixed": fixed, "skipped": skipped, "notes": notes.strip()}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python -m ci.engines.sdk <workdir> <findings.json>")
    with contextlib.suppress(ImportError):
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    data = json.loads(Path(sys.argv[2]).read_text())
    items = data if isinstance(data, list) else data.get("findings", [])
    print(json.dumps(fix(Path(sys.argv[1]), items, ["services/"]), indent=2))

"""Engine: Gemini + Google ADK, built by hand. We write the (deliberately narrow) tools.

CLI: python -m ci.engines.adk <workdir> <findings.json> [--allow services/ ...]
     python -m ci.engines.adk --selftest
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from ci.engines.prompt import RESULT_SCHEMA, build_prompt

MAX_LLM_CALLS = 80
WALL_SECONDS = 15 * 60
TEST_TIMEOUT = 300
TAIL = 4000


def _resolve(workdir: Path, rel: str, allowed_prefixes: list[str] | None = None) -> Path | str:
    """Return the resolved path inside workdir, or an error string."""
    if not isinstance(rel, str) or not rel.strip():
        return "error: empty path"
    if os.path.isabs(rel) or rel.startswith("~"):
        return "error: absolute paths are not allowed"
    if ".." in Path(rel).parts:
        return "error: '..' is not allowed"
    root = workdir.resolve()
    target = (root / rel).resolve()  # follows symlinks, so escapes show up here
    if target != root and root not in target.parents:
        return "error: path escapes the workdir"
    if allowed_prefixes is not None:
        relpath = target.relative_to(root).as_posix()
        if not any(relpath.startswith(p.rstrip("/") + "/") for p in allowed_prefixes):
            return f"error: writes only allowed under {', '.join(allowed_prefixes)}"
    return target


def _make_tools(workdir: Path, allowed_prefixes: list[str]):
    seen: set[Path] = set()  # files read this run; existing files must be read before they are overwritten

    def list_files(dir: str) -> str:
        """List files (recursively, relative paths) under a directory of the repo. Use "." for the root."""
        p = workdir.resolve() if dir in (".", "") else _resolve(workdir, dir)
        if isinstance(p, str):
            return p
        if not p.is_dir():
            return "error: not a directory"
        root = workdir.resolve()
        out = [
            f.relative_to(root).as_posix()
            for f in sorted(p.rglob("*"))
            if f.is_file() and not any(part in (".git", "__pycache__", ".pytest_cache") for part in f.parts)
        ]
        return "\n".join(out[:500]) or "(empty)"

    def read_file(path: str) -> str:
        """Read a text file, given its path relative to the repo root."""
        p = _resolve(workdir, path)
        if isinstance(p, str):
            return p
        if not p.is_file():
            return "error: no such file"
        try:
            text = p.read_text()
            seen.add(p)
            return text
        except Exception as e:  # binary, permissions, ...
            return f"error: {e}"

    def write_file(path: str, content: str) -> str:
        """Overwrite (or create) a file with the full new content. Path is relative to the repo root."""
        p = _resolve(workdir, path, allowed_prefixes)
        if isinstance(p, str):
            return p
        if p.exists() and p not in seen:
            return "error: read_file this file first; write_file replaces the whole file"
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            seen.add(p)
        except Exception as e:
            return f"error: {e}"
        return f"ok: wrote {len(content)} chars to {path}"

    def run_tests(service: str) -> str:
        """Run the pytest suite of one service (the directory name under services/, e.g. "loan-service")."""
        p = _resolve(workdir, f"services/{service}")
        if isinstance(p, str) or "/" in service:
            return p if isinstance(p, str) else "error: service must be a single directory name"
        if not p.is_dir():
            return "error: no such service"
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp"),
               "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=p, env=env,
                               capture_output=True, text=True, timeout=TEST_TIMEOUT)
        except subprocess.TimeoutExpired:
            return f"error: tests timed out after {TEST_TIMEOUT}s"
        return f"exit code: {r.returncode}\n{(r.stdout + r.stderr)[-TAIL:]}"

    return [list_files, read_file, write_file, run_tests]


def _result_model():
    """Pydantic mirror of RESULT_SCHEMA. ADK wraps a raw dict schema as a loose `response` arg
    to set_model_response, so the model is not actually constrained; a BaseModel is."""
    from pydantic import BaseModel, create_model

    class Skip(BaseModel):
        fingerprint: str
        reason: str

    fields = {"fixed": (list[str], ...), "skipped": (list[Skip], ...), "notes": (str, "")}
    assert set(fields) == set(RESULT_SCHEMA["properties"]), "RESULT_SCHEMA drifted from the ADK model"
    return create_model("FixResult", **fields)


def _parse_result(text) -> dict | None:
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        return None
    try:  # JSON-encoded string of JSON
        inner = json.loads(text)
        if isinstance(inner, (str, dict)) and inner != text:
            return _parse_result(inner)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return _parse_result(json.loads(m.group(0)))
    except json.JSONDecodeError:
        return None


async def _run(workdir: Path, findings: list[dict], allowed_prefixes: list[str]) -> tuple[dict | None, int]:
    from google.adk import Agent
    from google.adk.agents.run_config import RunConfig
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    calls = 0

    def count(callback_context, llm_request):
        nonlocal calls
        calls += 1
        return None

    agent = Agent(
        name="fixer",
        model=os.environ.get("ADK_MODEL", "gemini-2.5-pro"),
        instruction="You are a careful software engineer. Use only the tools given. Paths are relative to the repo root.",
        tools=_make_tools(workdir, allowed_prefixes),
        output_schema=_result_model(),
        output_key="result",
        before_model_callback=count,
    )
    runner = InMemoryRunner(agent=agent, app_name="adk-fix")
    session = await runner.session_service.create_session(app_name="adk-fix", user_id="ci")
    msg = types.Content(role="user", parts=[types.Part(text=build_prompt(findings, workdir, allowed_prefixes))])
    last_text = None
    async for ev in runner.run_async(user_id="ci", session_id=session.id, new_message=msg,
                                     run_config=RunConfig(max_llm_calls=MAX_LLM_CALLS)):
        if ev.content and ev.content.parts:
            for p in ev.content.parts:  # progress trace for the CI log (stderr, no secrets)
                if p.function_call:
                    print(f"[adk] call {p.function_call.name} {json.dumps(p.function_call.args)[:200]}", file=sys.stderr)
                elif p.text and not p.thought:
                    print(f"[adk] text {p.text[:300]!r}", file=sys.stderr)
            t ="".join(p.text for p in ev.content.parts if p.text and not p.thought)
            if t.strip():
                last_text = t
    session = await runner.session_service.get_session(app_name="adk-fix", user_id="ci", session_id=session.id)
    return _parse_result(session.state.get("result")) or _parse_result(last_text), calls


def fix(workdir: Path, findings: list[dict], allowed_prefixes: list[str]) -> dict:
    workdir = Path(workdir)
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except ImportError:
        pass
    # Gemini API, not Vertex; GEMINI_API_KEY is the only secret we touch.
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "false"
    all_fps = [f.get("fingerprint", "") for f in findings]
    t0 = time.monotonic()
    try:
        result, calls = asyncio.run(asyncio.wait_for(_run(workdir, findings, allowed_prefixes), WALL_SECONDS))
    except Exception as e:  # timeout, LLM-call cap, API error: report, don't crash the job
        return {"fixed": [], "skipped": [{"fingerprint": fp, "reason": f"engine error: {type(e).__name__}: {e}"}
                                         for fp in all_fps], "notes": "adk engine failed"}
    elapsed = time.monotonic() - t0
    if not result:
        return {"fixed": [], "skipped": [{"fingerprint": fp, "reason": "no structured result from agent"}
                                         for fp in all_fps], "notes": f"adk: {calls} model calls, {elapsed:.0f}s"}
    fixed = [fp for fp in result.get("fixed", []) if fp in all_fps]
    skipped = [s for s in result.get("skipped", []) if isinstance(s, dict) and s.get("fingerprint") in all_fps]
    seen = set(fixed) | {s["fingerprint"] for s in skipped}
    skipped += [{"fingerprint": fp, "reason": "not reported by agent"} for fp in all_fps if fp not in seen]
    notes = (result.get("notes") or "").strip()
    return {"fixed": fixed, "skipped": skipped, "notes": f"{notes} [adk: {calls} model calls, {elapsed:.0f}s]".strip()}


def _selftest() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        w = Path(d) / "w"
        (w / "services/a").mkdir(parents=True)
        (w / "services/a/x.py").write_text("x")
        (w / "AGENTS.md").write_text("rules")
        outside = Path(d) / "secret"
        outside.write_text("s")
        (w / "services/link").symlink_to(outside)
        ls, rd, wr, rt = _make_tools(w, ["services/"])
        assert rd("services/a/x.py") == "x"
        assert rd("AGENTS.md") == "rules"
        for bad in ("/etc/passwd", "../secret", "services/../../secret", "services/link", "~/x"):
            assert rd(bad).startswith("error"), bad
            assert wr(bad, "p").startswith("error"), bad
        assert wr("AGENTS.md", "p").startswith("error") and (w / "AGENTS.md").read_text() == "rules"
        assert wr("servicesX/y.py", "p").startswith("error")
        (w / "services/a/z.py").write_text("z")
        assert wr("services/a/z.py", "y").startswith("error: read_file")
        assert rd("services/a/z.py") == "z" and wr("services/a/z.py", "y").startswith("ok")
        assert wr("services/a/new.py", "ok").startswith("ok") and (w / "services/a/new.py").read_text() == "ok"
        assert outside.read_text() == "s"
        assert rt("../..").startswith("error") and rt("a/b").startswith("error")
        assert "services/a/x.py" in ls(".") and ls("..").startswith("error")
    assert _parse_result('```json\n{"fixed": ["a"], "skipped": []}\n```') == {"fixed": ["a"], "skipped": []}
    print("selftest ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
        sys.exit(0)
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    prefixes = sys.argv[sys.argv.index("--allow") + 1:] if "--allow" in sys.argv else ["services/"]
    print(json.dumps(fix(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()), prefixes), indent=2))

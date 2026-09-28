"""stage-3: ai-fix. One automated fix round on a pull request, in two steps.

    python -m ci.ai_fix fix --engine interactions|sdk|adk      # no push credential in env
    python -m ci.ai_fix publish                                 # ASSISTANT_TOKEN in env

`fix` hands the review findings to the chosen engine, then refuses the result unless
every changed path is under AI_FIX_ALLOWED_PATHS and the tests pass. `publish` commits
what `fix` accepted as Developer assistant and pushes it to the PR branch, which starts
the next pipeline run: tests, re-review, merge gate. That is the loop.

It stops when the review has nothing at or above AI_FIX_MIN_SEVERITY, or after
AI_FIX_MAX_ROUNDS fix commits are on the branch (the PR is labelled `needs-human`).
The two steps are separate CI steps so the agent never runs with the push token.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

SEVERITY_RANK = {"nit": 0, "minor": 1, "major": 2, "blocker": 3}
TRAILER = "AI-Fix-Round:"
RESULT = Path("fix-result.json")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def findings_to_fix(findings: list[dict], min_severity: str) -> list[dict]:
    floor = SEVERITY_RANK[min_severity]
    keep = [f for f in findings if SEVERITY_RANK.get(f.get("severity"), 0) >= floor]
    return sorted(keep, key=lambda f: -SEVERITY_RANK[f["severity"]])


def rounds_done(base_ref: str) -> int:
    log = git("log", f"{base_ref}..HEAD", "--format=%B")
    return sum(1 for line in log.splitlines() if line.startswith(TRAILER))


def changed_paths() -> list[str]:
    """Modified, deleted and new (untracked, not ignored) files, repo-relative."""
    tracked = git("diff", "--name-only", "HEAD").splitlines()
    new = git("ls-files", "--others", "--exclude-standard").splitlines()
    return sorted(set(tracked + new))


def disallowed(paths: list[str], allowed: list[str]) -> list[str]:
    return [p for p in paths if not any(p.startswith(a) for a in allowed)]


def run_tests(paths: list[str]) -> tuple[bool, str]:
    services = sorted({p.split("/")[1] for p in paths if p.startswith("services/") and p.count("/") > 1})
    out = []
    for name in services:
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                              cwd=f"services/{name}", capture_output=True, text=True,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        out.append(f"{name}: {(proc.stdout.strip().splitlines() or ['no output'])[-1]}")
        if proc.returncode != 0:
            return False, "\n".join(out)
    return True, "\n".join(out)


def cmd_fix(args: argparse.Namespace) -> int:
    e = os.environ
    findings = json.loads(Path(args.findings).read_text()) if Path(args.findings).is_file() else []
    todo = findings_to_fix(findings, e.get("AI_FIX_MIN_SEVERITY", "major"))
    done = rounds_done(e.get("AI_FIX_BASE_REF", "origin/main"))
    limit = int(e.get("AI_FIX_MAX_ROUNDS", "3"))
    result = {"engine": args.engine, "round": done + 1, "todo": len(todo)}

    if not todo:
        result["status"] = "nothing"
    elif done >= limit:
        result.update(status="limit", round=done)
    else:
        engine = importlib.import_module(f"ci.engines.{args.engine}")
        allowed = e.get("AI_FIX_ALLOWED_PATHS", "services/").split(",")
        result.update(engine.fix(Path.cwd(), todo, allowed))
        paths = changed_paths()
        bad = disallowed(paths, allowed)
        ok, tests = run_tests(paths)
        result.update(changed=paths, tests=tests)
        if bad:
            # Put the tree back so nothing outside the allowed paths can reach `publish`.
            git("checkout", "--", ".")
            git("clean", "-fdq")
            result.update(status="disallowed", disallowed=bad)
        elif not paths:
            result["status"] = "no-change"
        else:
            result["status"] = "fixed" if ok else "tests-failed"

    RESULT.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "notes"}, indent=2))
    return 0


class GitHub:
    def __init__(self, token: str, repo: str, pr: int):
        self.pr = pr
        self.http = httpx.Client(base_url=f"https://api.github.com/repos/{repo}", timeout=30, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})

    def comment(self, body: str) -> None:
        self.http.post(f"/issues/{self.pr}/comments", json={"body": body}).raise_for_status()

    def label(self, name: str) -> None:
        self.http.post(f"/issues/{self.pr}/labels", json={"labels": [name]}).raise_for_status()

    def reply_on_finding(self, fingerprint: str, body: str) -> None:
        marker = f"antigravity-reviewer:fp={fingerprint}"
        for c in self.http.get(f"/pulls/{self.pr}/comments", params={"per_page": 100}).json():
            if marker in (c.get("body") or "") and not c.get("in_reply_to_id"):
                self.http.post(f"/pulls/{self.pr}/comments/{c['id']}/replies", json={"body": body})
                return


def cmd_publish(args: argparse.Namespace) -> int:
    e = os.environ
    result = json.loads(RESULT.read_text())
    gh = GitHub(e["ASSISTANT_TOKEN"], e["GITHUB_REPOSITORY"], int(e["PR_NUMBER"]))
    status, n, engine = result["status"], result["round"], result["engine"]

    if status == "nothing":
        print("ai-fix: nothing to fix")
        return 0
    if status == "limit":
        gh.label("needs-human")
        gh.comment(f"🤖 **ai-fix ({engine})**: {n} fix rounds done and {result['todo']} finding(s) "
                   "still at or above the fix threshold. Handing over to a human.")
        return 0
    # The agent's reasoning belongs on the finding's own thread, where the developer decides.
    for s in result.get("skipped", []):
        gh.reply_on_finding(s["fingerprint"], f"🤖 ai-fix ({engine}) left this unchanged: {s['reason']}\n\n"
                            "Agree? Resolve this conversation: the next review stops counting it.")

    if status != "fixed":
        gh.comment(f"🤖 **ai-fix ({engine}) round {n}: not pushed**, status `{status}`.\n\n"
                   f"```\n{json.dumps({k: result.get(k) for k in ('changed', 'disallowed', 'tests', 'notes')}, indent=2)}\n```")
        return 1 if status in ("disallowed", "tests-failed") else 0

    git("add", "-A", "--", *result["changed"])
    git("-c", "user.name=Developer assistant", "-c", f"user.email={e['ASSISTANT_EMAIL']}",
        "commit", "-q", "-m", f"stage 3: ai-fix round {n} ({engine})",
        "-m", f"Automated fix of {len(result.get('fixed', []))} review finding(s) by the {engine} engine.",
        "-m", f"{TRAILER} {n}")
    remote = f"https://x-access-token:{e['ASSISTANT_TOKEN']}@github.com/{e['GITHUB_REPOSITORY']}.git"
    # A fast-forward push only: if someone pushed meanwhile it fails, and their run owns the PR.
    git("push", remote, f"HEAD:refs/heads/{e['HEAD_REF']}")
    sha = git("rev-parse", "--short", "HEAD")

    fixed = result.get("fixed", [])
    gh.comment(f"🤖 **ai-fix ({engine}) round {n}** pushed `{sha}`: fixed {len(fixed)}, "
               f"skipped {len(result.get('skipped', []))}.\n\nChanged: "
               + ", ".join(f"`{p}`" for p in result["changed"]) + f"\n\nTests: `{result['tests']}`\n\n"
               "The re-review in the next run resolves the threads it no longer reports.")
    print(f"ai-fix: pushed {sha}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fix")
    f.add_argument("--engine", required=True, choices=["interactions", "sdk", "adk"])
    f.add_argument("--findings", default="ai-review-findings.json")
    sub.add_parser("publish")
    args = parser.parse_args()
    return cmd_fix(args) if args.cmd == "fix" else cmd_publish(args)


if __name__ == "__main__":
    sys.exit(main())

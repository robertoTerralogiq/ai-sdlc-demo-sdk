#!/usr/bin/env python
"""Run the LOAN-12 pull request pipeline locally, round by round.

Each round is one push to the PR branch and runs what `.github/workflows/ci.yml` runs:

    tests      pytest in services/loan-service
    ai-review  the real reviewer, real Gemini call, same prompt, anchoring, dedup, gate
    merge?     auto-merge fires only when tests pass and the gate does not trip

    round 1  main + demo/mr-fixture/             the flawed first draft
    round 2  round 1 + demo/fix-round-1/         what address-review is asked to produce

Only GitHub is faked: comments go to an in-memory PR that persists across rounds,
so round 2 shows the reviewer skipping what it already posted. The round-2 code is a
committed stand-in for the agent's fix, not live agent output.

    GEMINI_API_KEY=... python demo/run_pipeline.py      # output in demo/out/round-N/
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from antigravity_reviewer.anchor import anchor_findings, dedupe, filter_and_rank
from antigravity_reviewer.cli import gate_tripped
from antigravity_reviewer.codequality import to_codequality
from antigravity_reviewer.config import Settings
from antigravity_reviewer.diff import FileDiff, parse_file_diff
from antigravity_reviewer.publisher import Publisher, build_summary
from antigravity_reviewer.reviewer import GeminiReviewer

DEMO = Path(__file__).resolve().parent
REPO = DEMO.parent
MR_TITLE = "LOAN-12: Early settlement quote"
MR_DESCRIPTION = (DEMO / "TICKET-LOAN-12.md").read_text()
DIFF_REFS = {"base_sha": "base0000", "start_sha": "base0000", "head_sha": "head1111"}
ROUNDS = [("round 1: first draft pushed", ["mr-fixture"]),
          ("round 2: review addressed", ["mr-fixture", "fix-round-1"])]


class FakeMergeRequest:
    """Stands in for the GitHub PR. Keeps its comments across rounds, like a real one."""

    def __init__(self):
        self.comments: List[Dict[str, Any]] = []
        self.summary: str | None = None

    def existing_comments(self) -> List[Dict[str, Any]]:
        return list(self.comments)

    def existing_note_bodies(self) -> List[str]:
        return [c["body"] for c in self.comments]

    def create_inline_discussion(self, body, position) -> None:
        self.comments.append({"body": body, "new_path": position["new_path"],
                              "new_line": position.get("new_line")})

    def find_note_by_marker(self, marker):
        return "summary" if self.summary and marker in self.summary else None

    def create_note(self, body) -> None:
        self.summary = body

    def update_note(self, handle, body) -> None:
        self.summary = body


def build_tree(root: Path, overlays: List[str]) -> Path:
    shutil.copytree(REPO / "services", root / "services",
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    for overlay in overlays:
        shutil.copytree(DEMO / overlay, root, dirs_exist_ok=True)
    return root


def collect_diffs(base: Path, head: Path, settings: Settings) -> List[FileDiff]:
    """Diff the two trees the way GitHub reports a PR's changes (target → source)."""
    diffs = []
    for p in sorted(head.rglob("*")):
        path = p.relative_to(head).as_posix()
        if not p.is_file() or settings.is_excluded(path):
            continue
        old = base / path
        old_lines = old.read_text().splitlines() if old.is_file() else []
        new_lines = p.read_text().splitlines()
        if old_lines == new_lines:
            continue
        body = "\n".join(difflib.unified_diff(old_lines, new_lines, path, path, lineterm=""))
        diffs.append(parse_file_diff(body, old_path=path, new_path=path, new_file=not old.is_file(),
                                     max_lines=settings.max_diff_lines_per_file))
    return diffs


def run_tests(tree: Path) -> bool:
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                          cwd=tree / "services/loan-service", capture_output=True, text=True,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    print("   ", proc.stdout.strip().splitlines()[-1])
    return proc.returncode == 0


def ai_review(base: Path, head: Path, mr: FakeMergeRequest, settings: Settings, out: Path) -> bool:
    files = collect_diffs(base, head, settings)
    guidelines = (REPO / ".github/review-guidelines.md").read_text()
    outcome = GeminiReviewer(settings, guidelines=guidelines).review(
        mr_title=MR_TITLE, mr_description=MR_DESCRIPTION, target_branch="main", files=files,
        context_provider=lambda path: (head / path).read_text() if (head / path).is_file() else None)
    if outcome.failed_batches:
        raise SystemExit(f"model call failed for {outcome.failed_batches}; CI would exit 3")

    items, dropped = filter_and_rank(dedupe(anchor_findings(outcome.findings, files)),
                                     min_confidence=settings.min_confidence,
                                     max_findings=settings.max_findings)
    before = len(mr.comments)
    publisher = Publisher(mr, DIFF_REFS)
    report = publisher.publish_inline(items)
    tripped = gate_tripped(items, settings.fail_on)
    summary = build_summary(items=items, unanchored=report.unanchored,
                            model_summaries=outcome.summaries,
                            reviewed_files=[f.path for f in files], skipped_paths=[],
                            dropped_by_cap=dropped, failed_batches=outcome.failed_batches,
                            gate=settings.fail_on, gate_tripped=tripped, model=settings.model)
    publisher.publish_summary(summary)

    out.mkdir(parents=True, exist_ok=True)
    (out / "summary-note.md").write_text(summary)
    (out / "codequality.json").write_text(json.dumps(to_codequality(items), indent=2))
    (out / "inline-comments.md").write_text("\n\n---\n\n".join(
        f"### {c['new_path']}:{c['new_line']}\n\n{c['body']}" for c in mr.comments[before:]))

    by_sev: Dict[str, int] = {}
    for item in items:
        sev = item.finding.severity.value
        by_sev[sev] = by_sev.get(sev, 0) + 1
    print(f"    {len(items)} finding(s) {by_sev}, {len(mr.comments) - before} new comment(s), "
          f"{report.skipped_existing} already posted")
    for item in items:
        print(f"      {item.finding.severity.value:8} {item.new_path}:{item.new_line}  {item.finding.title}")
    return not tripped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-2.5-pro"))
    parser.add_argument("--out", default=str(DEMO / "out"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)

    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set.", file=sys.stderr)
        return 2
    settings = Settings(api_url="https://api.github.example.com", api_token="simulated",
                        repo="0", pr_number=12, model=args.model,
                        api_key=os.environ["GEMINI_API_KEY"], include_file_context=True)

    tmp = Path(tempfile.mkdtemp(prefix="loan-12-"))
    base = build_tree(tmp / "main", [])
    mr = FakeMergeRequest()
    print(f"PR #12 {MR_TITLE}  (auto-merge: on)")
    for n, (label, overlays) in enumerate(ROUNDS, 1):
        head = build_tree(tmp / f"round-{n}", overlays)
        print(f"\n── pipeline #{n}  {label}")
        print("  stage test: loan-service:tests")
        tests_ok = run_tests(head)
        print(f"  stage review: ai-review (gate: {settings.fail_on})")
        review_ok = ai_review(base, head, mr, settings, Path(args.out) / f"round-{n}")
        status = "passed" if tests_ok and review_ok else "failed"
        print(f"  pipeline #{n} {status}", end="")
        if status == "passed":
            print(" → auto-merge: LOAN-12 merged into main")
            return 0
        print(" → merge blocked, PR waits for a fix push")
    print("\nno round passed; PR stays open for a human")
    return 1


if __name__ == "__main__":
    sys.exit(main())

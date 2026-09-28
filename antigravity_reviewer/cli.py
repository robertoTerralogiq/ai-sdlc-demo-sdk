"""Entry point: read the pull request, review it, publish, then set the exit code."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from dotenv import find_dotenv, load_dotenv

from .anchor import anchor_findings, dedupe, filter_and_rank
from .codequality import write_codequality, write_findings_json
from .config import ConfigError, Settings
from .github_client import GitHubClient
from .models import SEVERITY_RANK, Severity
from .publisher import Publisher, build_summary
from .reviewer import GeminiReviewer

log = logging.getLogger("antigravity_reviewer")

GUIDELINES_PATHS = (".github/review-guidelines.md",)

IDE_STATUS_CONTEXT = "stage-2: ai-review (ide)"

EXIT_OK = 0
EXIT_GATE = 1
EXIT_CONFIG = 2
EXIT_ERROR = 3


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="antigravity-review",
        description="Review a GitHub pull request with Gemini and post the findings.",
    )
    parser.add_argument("--pr", type=int, help="pull request number (defaults to REVIEW_PR_NUMBER)")
    parser.add_argument(
        "--env-file",
        help="path to a .env file (defaults to the nearest .env at or above the working directory)",
    )
    parser.add_argument(
        "--no-env-file",
        action="store_true",
        help="ignore any .env file and read the environment only",
    )
    parser.add_argument("--model", help="model id (defaults to GEMINI_MODEL)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="review and write artifacts, but post nothing to GitHub",
    )
    parser.add_argument("--no-inline", action="store_true", help="summary comment only")
    parser.add_argument("--no-summary", action="store_true", help="inline comments only")
    parser.add_argument(
        "--fail-on",
        choices=["blocker", "major", "minor", "nit", "never"],
        help="lowest severity that fails the check (defaults to REVIEW_FAIL_ON)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def load_guidelines() -> Optional[str]:
    """Optional per-repo prompt addendum, so teams can encode their own rules."""
    for candidate in GUIDELINES_PATHS:
        path = Path(candidate)
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                log.info("loaded review guidelines from %s", candidate)
                return text[:8000]
    return None


def load_env_file(args: argparse.Namespace) -> Optional[str]:
    """Load a .env file for local runs, without letting it shadow the real environment.

    `override=False` is the important part: in CI the predefined variables and the
    masked project variables are already set, and a .env that someone committed by
    accident must never win over them.

    Returns the path that was loaded, or None. An explicit --env-file that does not
    exist is an error rather than a silent fallback.
    """
    if args.no_env_file:
        return None

    if args.env_file:
        path = Path(args.env_file)
        if not path.is_file():
            raise ConfigError(f"--env-file {args.env_file} does not exist")
    else:
        found = find_dotenv(usecwd=True)
        if not found:
            return None
        path = Path(found)

    load_dotenv(path, override=False)
    log.info("loaded environment defaults from %s", path)
    return str(path)


def apply_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    if args.pr:
        settings.pr_number = args.pr
    if args.model:
        settings.model = args.model
    if args.dry_run:
        settings.dry_run = True
    if args.no_inline:
        settings.post_inline = False
    if args.no_summary:
        settings.post_summary = False
    if args.fail_on:
        settings.fail_on = None if args.fail_on == "never" else args.fail_on
    return settings


def gate_tripped(items, fail_on: Optional[str]) -> bool:
    if not fail_on:
        return False
    threshold = SEVERITY_RANK[Severity(fail_on)]
    return any(SEVERITY_RANK[item.finding.severity] >= threshold for item in items)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    try:
        load_env_file(args)
        settings = apply_overrides(Settings.from_env(pr_number=args.pr), args)
    except ConfigError as exc:
        log.error("%s", exc)
        return EXIT_CONFIG

    try:
        client = GitHubClient(settings)
        mr = client.load_merge_request()
    except Exception as exc:  # noqa: BLE001 - surface the cause, do not fail the MR silently
        log.error("could not load pull request #%s: %s", settings.pr_number, exc)
        return EXIT_ERROR

    log.info(
        "PR #%s %r: %d reviewable file(s), %d skipped",
        mr.iid,
        mr.title,
        len(mr.files),
        len(mr.skipped_paths),
    )

    publisher = Publisher(client, mr.diff_refs, dry_run=settings.dry_run)

    if not mr.files:
        write_codequality([], settings.codequality_path)
        write_findings_json([], settings.findings_json_path)
        if settings.post_summary:
            publisher.publish_summary(
                build_summary(
                    items=[],
                    unanchored=[],
                    model_summaries=["No reviewable text changes in this pull request."],
                    reviewed_files=[],
                    skipped_paths=mr.skipped_paths,
                    dropped_by_cap=0,
                    failed_batches=0,
                    gate=settings.fail_on,
                    gate_tripped=False,
                    model=settings.model,
                )
            )
        return EXIT_OK

    head_sha = mr.diff_refs.get("head_sha") or mr.source_branch

    try:
        reviewer = GeminiReviewer(settings, guidelines=load_guidelines())
        outcome = reviewer.review(
            mr_title=mr.title,
            mr_description=mr.description,
            target_branch=mr.target_branch,
            files=mr.files,
            context_provider=lambda path: client.file_content(path, head_sha),
        )
    except Exception as exc:  # noqa: BLE001
        log.error("review failed: %s", exc)
        return EXIT_ERROR

    if outcome.failed_batches == outcome.batches:
        log.error("every batch failed; not publishing a misleading clean review")
        return EXIT_ERROR

    anchored = dedupe(anchor_findings(outcome.findings, mr.files))
    items, dropped = filter_and_rank(
        anchored,
        min_confidence=settings.min_confidence,
        max_findings=settings.max_findings,
    )
    log.info(
        "%d raw finding(s) -> %d published (%d dropped by cap)",
        len(outcome.findings),
        len(items),
        dropped,
    )

    write_codequality(items, settings.codequality_path)
    write_findings_json(items, settings.findings_json_path)

    unanchored = [item for item in items if not item.is_anchored]
    if settings.post_inline:
        report = publisher.publish_inline(items)
        unanchored = report.unanchored
        log.info(
            "posted %d inline, skipped %d already present, %d rejected position(s)",
            report.posted_inline,
            report.skipped_existing,
            len(report.failed),
        )

        if settings.resolve_stale:
            log.info("resolved %d stale review thread(s)", publisher.resolve_stale(items, head_sha))

    accepted = set() if settings.dry_run else publisher.accepted_by_humans(items)
    if accepted:
        log.info("%d finding(s) accepted by a person (resolved thread), not gating", len(accepted))
        # ai-fix reads this file; an accepted finding is not work for it either.
        write_findings_json([i for i in items if i.finding.fingerprint() not in accepted],
                            settings.findings_json_path)
    tripped = gate_tripped([i for i in items if i.finding.fingerprint() not in accepted], settings.fail_on)

    if settings.post_summary:
        summary_body = build_summary(
            items=items,
            unanchored=unanchored,
            model_summaries=outcome.summaries,
            reviewed_files=[f.path for f in mr.files],
            skipped_paths=mr.skipped_paths,
            dropped_by_cap=dropped,
            failed_batches=outcome.failed_batches,
            gate=settings.fail_on,
            gate_tripped=tripped,
            model=settings.model,
        )
        publisher.publish_summary(summary_body)
        # Shown on the Actions run page, so the review is visible without opening the PR.
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
                fh.write(summary_body + "\n")

    # Outside Actions (IDE mode) nothing else records that this commit was reviewed,
    # so post a status on it. Branch protection requires it, and a new push lacks it.
    if not os.environ.get("GITHUB_ACTIONS") and not settings.dry_run:
        state = "failure" if tripped else "success"
        client.set_status(head_sha, state, f"{len(items)} finding(s), gate: {settings.fail_on}",
                          IDE_STATUS_CONTEXT)
        log.info("set commit status %r = %s on %s", IDE_STATUS_CONTEXT, state, head_sha[:7])

    if tripped:
        log.error("gate: at least one finding is %s or worse", settings.fail_on)
        return EXIT_GATE
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

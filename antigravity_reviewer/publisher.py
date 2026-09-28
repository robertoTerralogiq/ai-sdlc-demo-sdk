"""Turn findings into pull request comments.

Comment bodies carry a hidden fingerprint marker. That marker is what makes the
job idempotent: every pipeline run reads the existing notes first and skips any
finding it has already published, so pushing a fixup commit does not repost the
whole review.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .models import SEVERITY_EMOJI, AnchoredFinding, Severity
from .similarity import Signature, is_near_duplicate, signature

log = logging.getLogger(__name__)

MARKER_PREFIX = "antigravity-reviewer"
SUMMARY_MARKER = f"<!-- {MARKER_PREFIX}:summary -->"
FINGERPRINT_RE = re.compile(rf"<!-- {MARKER_PREFIX}:fp=([0-9a-f]+) -->")
# Matches the header line written by `format_finding_body`:
# "{emoji} **{severity}** · `{category}` — {title}"
HEADER_RE = re.compile(r"^\S+\s+\*\*(.+?)\*\*\s+·\s+`(.+?)`\s+—\s+(.+)$", re.MULTILINE)


@dataclass
class PublishReport:
    posted_inline: int = 0
    posted_fallback: int = 0
    skipped_existing: int = 0
    failed: List[str] = field(default_factory=list)
    unanchored: List[AnchoredFinding] = field(default_factory=list)


def fingerprint_marker(fingerprint: str) -> str:
    return f"<!-- {MARKER_PREFIX}:fp={fingerprint} -->"


def existing_fingerprints(bodies: Sequence[str]) -> set:
    found = set()
    for body in bodies:
        found.update(FINGERPRINT_RE.findall(body or ""))
    return found


def _title_from_body(body: str) -> Optional[str]:
    """Recover the title from a comment header, or None if it does not parse."""
    match = HEADER_RE.search(body or "")
    if match is None:
        return None
    return match.group(3)


def _published_signatures(comments: Sequence[Dict[str, Any]]) -> List[Signature]:
    """Reduce raw comment dicts to the `Signature` shape near-dup checks need.

    Only comments that carry a fingerprint marker and a parseable header are
    considered; a comment without either is not one of ours to compare against.
    """
    signatures: List[Signature] = []
    for comment in comments:
        body = comment.get("body") or ""
        if not FINGERPRINT_RE.search(body):
            continue
        title = _title_from_body(body)
        if title is None:
            continue
        signatures.append(
            signature(comment.get("new_path"), comment.get("new_line"), title)
        )
    return signatures


def format_finding_body(item: AnchoredFinding, *, include_suggestion: bool) -> str:
    finding = item.finding
    emoji = SEVERITY_EMOJI[finding.severity]
    lines = [
        fingerprint_marker(finding.fingerprint()),
        f"{emoji} **{finding.severity.value}** · `{finding.category}` — {finding.title}",
        "",
        finding.detail.strip(),
    ]
    if include_suggestion and finding.suggestion.strip():
        # GitHub renders this as a one-click suggestion replacing the commented line.
        lines += ["", "```suggestion", finding.suggestion.rstrip(), "```"]
    elif finding.suggestion.strip():
        lines += ["", "Suggested change:", "```", finding.suggestion.rstrip(), "```"]
    footer = f"Automated review · confidence {finding.confidence:.2f}"
    if item.snapped:
        footer += f" · cited line {finding.line}, pinned to the nearest changed line"
    lines += ["", f"<sub>{footer}</sub>"]
    return "\n".join(lines)


def build_position(item: AnchoredFinding, diff_refs: Dict[str, str]) -> Dict[str, Any]:
    position: Dict[str, Any] = {
        "position_type": "text",
        "base_sha": diff_refs.get("base_sha"),
        "start_sha": diff_refs.get("start_sha"),
        "head_sha": diff_refs.get("head_sha"),
        "new_path": item.new_path,
        "old_path": item.old_path or item.new_path,
    }
    if item.new_line is not None:
        position["new_line"] = item.new_line
    if item.old_line is not None:
        position["old_line"] = item.old_line
    return position


class Publisher:
    def __init__(self, client, diff_refs: Dict[str, str], *, dry_run: bool = False):
        self.client = client
        self.diff_refs = diff_refs
        self.dry_run = dry_run

    def publish_inline(self, items: Sequence[AnchoredFinding]) -> PublishReport:
        report = PublishReport()
        existing_comments: List[Dict[str, Any]] = []
        if not self.dry_run:
            try:
                existing_comments = self.client.existing_comments()
            except Exception as exc:  # noqa: BLE001 - a narrow token scope must not crash the run
                log.warning(
                    "cannot list existing comments (%s); duplicate detection is skipped "
                    "for this run",
                    exc,
                )
                existing_comments = []

        already = existing_fingerprints(c["body"] for c in existing_comments)
        published = _published_signatures(existing_comments)

        for item in items:
            fingerprint = item.finding.fingerprint()
            if fingerprint in already:
                report.skipped_existing += 1
                continue

            candidate = signature(item.finding.file, item.new_line, item.finding.title)
            if any(is_near_duplicate(candidate, sig) for sig in published):
                report.skipped_existing += 1
                continue

            if not item.is_anchored:
                report.unanchored.append(item)
                continue

            # An applicable suggestion is only offered when the model cited a
            # line that is genuinely in the diff. On a snapped anchor the fix
            # goes in as a plain code block, so nobody can one-click it onto a
            # line the model never looked at.
            body = format_finding_body(item, include_suggestion=not item.snapped)
            position = build_position(item, self.diff_refs)

            if self.dry_run:
                log.info("[dry-run] inline %s:%s %s", item.new_path, item.new_line, item.finding.title)
                report.posted_inline += 1
                continue

            try:
                self.client.create_inline_discussion(body, position)
                report.posted_inline += 1
            except Exception as exc:  # noqa: BLE001 - a rejected position must not abort the run
                log.warning(
                    "inline comment rejected for %s:%s (%s); falling back to the summary",
                    item.new_path,
                    item.new_line,
                    exc,
                )
                report.failed.append(f"{item.finding.file}:{item.finding.line}")
                report.unanchored.append(item)
            already.add(fingerprint)
            published.append(candidate)

        return report

    def accepted_by_humans(self, items: Sequence[AnchoredFinding]) -> set:
        """Fingerprints of current findings whose thread a person resolved.

        Resolving a thread is how a developer says "seen, accepted" (a false positive, or
        a follow-up ticket). Those findings stop counting toward the gate. Only threads
        resolved by someone other than the reviewer count, so the reviewer's own
        clean-up of stale threads can never wave a finding through.
        """
        try:
            threads = self.client.human_resolved_threads()
        except Exception as exc:  # noqa: BLE001 - without the lookup every finding still counts
            log.warning("cannot list resolved threads (%s); no finding is treated as accepted", exc)
            return set()
        accepted = set()
        for item in items:
            fp = item.finding.fingerprint()
            sig = signature(item.finding.file, item.new_line, item.finding.title)
            for thread in threads:
                fps = FINGERPRINT_RE.findall(thread["body"])
                title = _title_from_body(thread["body"])
                same = bool(fps) and fps[0] == fp
                near = (title is not None and thread.get("new_line") is not None and is_near_duplicate(
                    signature(thread.get("new_path"), thread["new_line"], title), sig))
                if same or near:
                    accepted.add(fp)
                    break
        return accepted

    def resolve_stale(self, items: Sequence[AnchoredFinding], head_sha: str) -> int:
        """Resolve our own open threads that this review no longer reports.

        A thread is kept open while its fingerprint, or a near-duplicate of it, is
        still among the current findings. Everything else was fixed or was a false
        positive the model dropped; either way the conversation stops blocking merge.
        The reply says "not reported", not "fixed", because we cannot tell which.
        """
        if self.dry_run:
            return 0
        current_fps = {item.finding.fingerprint() for item in items}
        current = [signature(i.finding.file, i.new_line, i.finding.title) for i in items]
        resolved = 0
        try:
            threads = self.client.unresolved_bot_threads()
        except Exception as exc:  # noqa: BLE001 - a narrow token scope must not crash the run
            log.warning("cannot list review threads (%s); stale threads stay open", exc)
            return 0
        for thread in threads:
            fps = FINGERPRINT_RE.findall(thread["body"])
            if not fps or fps[0] in current_fps:
                continue
            title = _title_from_body(thread["body"])
            if title and thread.get("new_line") is not None:
                old = signature(thread.get("new_path"), thread["new_line"], title)
                if any(is_near_duplicate(old, sig) for sig in current):
                    continue
            try:
                self.client.resolve_thread(
                    thread["id"],
                    f"Not reported by the review of `{head_sha[:7]}`, resolving. "
                    "Reopen if it still applies.",
                )
                resolved += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("could not resolve thread %s: %s", thread["id"], exc)
        return resolved

    def publish_summary(self, body: str) -> bool:
        """Post or replace the summary note. Returns whether it landed.

        A failure here must not take down a run whose inline comments already
        posted, so every GitHub call in this path is contained.
        """
        if self.dry_run:
            log.info("[dry-run] summary note:\n%s", body)
            return True

        existing = None
        try:
            existing = self.client.find_note_by_marker(SUMMARY_MARKER)
        except Exception as exc:  # noqa: BLE001 - reading the thread needs a wider scope
            log.warning(
                "could not look for an existing summary note (%s); posting a new one", exc
            )

        try:
            if existing is not None:
                # Replace the previous verdict rather than stacking a new one on every push.
                self.client.update_note(existing, body)
            else:
                self.client.create_note(body)
            return True
        except Exception as exc:  # noqa: BLE001 - the inline comments are already published
            log.error("could not post the summary note: %s", exc)
            return False


def build_summary(
    *,
    items: Sequence[AnchoredFinding],
    unanchored: Sequence[AnchoredFinding],
    model_summaries: Sequence[str],
    reviewed_files: Sequence[str],
    skipped_paths: Sequence[str],
    dropped_by_cap: int,
    failed_batches: int,
    gate: Optional[str],
    gate_tripped: bool,
    model: str,
) -> str:
    counts = {severity: 0 for severity in Severity}
    for item in items:
        counts[item.finding.severity] += 1

    lines = [SUMMARY_MARKER, "## Automated code review", ""]

    if not items:
        lines.append("No blocking issues found in the changed lines.")
    else:
        verdict = (
            "**Changes requested.** A blocking issue is listed below."
            if gate_tripped
            else "Review comments posted inline."
        )
        lines.append(verdict)
        lines.append("")
        lines.append("| Severity | Count |")
        lines.append("| --- | --- |")
        for severity in Severity:
            if counts[severity]:
                lines.append(
                    f"| {SEVERITY_EMOJI[severity]} {severity.value} | {counts[severity]} |"
                )
        lines.append("")
        lines.append("| Severity | File | Finding |")
        lines.append("| --- | --- | --- |")
        for item in items:
            finding = item.finding
            location = f"`{finding.file}:{finding.line}`"
            title = finding.title.replace("|", "\\|")
            lines.append(
                f"| {SEVERITY_EMOJI[finding.severity]} {finding.severity.value} "
                f"| {location} | {title} |"
            )

    if unanchored:
        lines += ["", "<details><summary>Findings that could not be pinned to a diff line</summary>", ""]
        for item in unanchored:
            finding = item.finding
            lines.append(f"- **{finding.severity.value}** `{finding.file}:{finding.line}` — {finding.title}")
            lines.append(f"  {finding.detail.strip()}")
        lines += ["", "</details>"]

    if model_summaries:
        lines += ["", "<details><summary>Reviewer notes</summary>", ""]
        lines += [f"{summary}\n" for summary in model_summaries]
        lines += ["</details>"]

    lines += ["", "<details><summary>Scope of this review</summary>", ""]
    lines.append(f"Model: `{model}`")
    lines.append("")
    lines.append(f"Reviewed {len(reviewed_files)} file(s):")
    lines += [f"- `{path}`" for path in reviewed_files]
    if skipped_paths:
        lines.append("")
        lines.append("Not reviewed:")
        lines += [f"- `{path}`" for path in skipped_paths]
    if dropped_by_cap:
        lines.append("")
        lines.append(
            f"{dropped_by_cap} lower-severity finding(s) omitted by REVIEW_MAX_FINDINGS."
        )
    if failed_batches:
        lines.append("")
        lines.append(
            f"{failed_batches} batch(es) failed to review after retries; coverage is incomplete."
        )
    lines += ["", "</details>"]

    if gate:
        lines += ["", f"<sub>Pipeline gate: fails on `{gate}` or worse.</sub>"]

    return "\n".join(lines)

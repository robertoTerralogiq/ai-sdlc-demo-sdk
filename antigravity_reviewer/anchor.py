"""Match model findings against the real diff, then filter and rank them.

Two failure modes are handled here. The model can cite a line that is not part of
the diff, which the GitHub review-comments API rejects outright, and it can repeat the
same problem across batches or across pipeline runs.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .diff import ADDED, CONTEXT, FileDiff
from .models import SEVERITY_RANK, AnchoredFinding, Finding
from .similarity import Signature, is_near_duplicate, signature

log = logging.getLogger(__name__)

# How far to search for a nearby added line when the cited number misses.
ANCHOR_SLACK = 5


def anchor_findings(
    findings: Sequence[Finding],
    files: Sequence[FileDiff],
) -> List[AnchoredFinding]:
    """Attach a postable diff position to each finding where possible."""
    by_path: Dict[str, FileDiff] = {f.path: f for f in files}
    anchored: List[AnchoredFinding] = []

    for finding in findings:
        file_diff = by_path.get(finding.file)
        if file_diff is None:
            log.info("dropping finding for unknown file %s: %s", finding.file, finding.title)
            continue

        position = _resolve_line(file_diff, finding.line)
        if position is None:
            # Keep it, but only as a file-level entry in the summary. Posting an
            # invalid position would fail the API call for the whole run.
            anchored.append(
                AnchoredFinding(
                    finding=finding,
                    new_path=file_diff.new_path,
                    old_path=file_diff.old_path,
                )
            )
            continue

        new_line, old_line, snapped = position
        anchored.append(
            AnchoredFinding(
                finding=finding,
                new_line=new_line,
                old_line=old_line,
                new_path=file_diff.new_path,
                old_path=file_diff.old_path,
                snapped=snapped,
            )
        )
    return anchored


def _resolve_line(
    file_diff: FileDiff, cited: int
) -> Optional[Tuple[Optional[int], Optional[int], bool]]:
    """Return (new_line, old_line, snapped) for a citable position, or None.

    An added line is addressed by `new_line` alone. An unchanged context line
    needs both numbers, otherwise the forge cannot place it in the side-by-side view.
    """
    exact = {
        line.new_line: line
        for line in file_diff.lines
        if line.new_line is not None and line.kind in (ADDED, CONTEXT)
    }
    line = exact.get(cited)
    if line is not None:
        if line.kind == ADDED:
            return line.new_line, None, False
        return line.new_line, line.old_line, False

    # The model was close but not exact, which happens around removed lines.
    # Snap to the nearest added line inside the slack window.
    added = sorted(file_diff.added_line_numbers)
    if not added:
        return None
    nearest = min(added, key=lambda n: (abs(n - cited), n))
    if abs(nearest - cited) <= ANCHOR_SLACK:
        log.debug("snapped %s:%d to nearest added line %d", file_diff.path, cited, nearest)
        return nearest, None, True
    return None


def dedupe(anchored: Sequence[AnchoredFinding]) -> List[AnchoredFinding]:
    """Collapse repeats of the same fingerprint or near-duplicate title within a run."""
    seen: Set[str] = set()
    kept: List[Signature] = []
    out: List[AnchoredFinding] = []
    for item in anchored:
        fingerprint = item.finding.fingerprint()
        if fingerprint in seen:
            continue

        candidate = signature(item.finding.file, item.new_line, item.finding.title)
        if any(is_near_duplicate(candidate, kept_sig) for kept_sig in kept):
            continue

        seen.add(fingerprint)
        kept.append(candidate)
        out.append(item)
    return out


def filter_and_rank(
    anchored: Sequence[AnchoredFinding],
    *,
    min_confidence: float,
    max_findings: int,
) -> Tuple[List[AnchoredFinding], int]:
    """Drop low-confidence findings, sort by severity, cap the count.

    Returns the kept findings and how many were dropped by the cap, so the
    summary can say so instead of silently truncating.
    """
    kept = [a for a in anchored if a.finding.confidence >= min_confidence]
    kept.sort(
        key=lambda a: (
            -SEVERITY_RANK[a.finding.severity],
            -a.finding.confidence,
            a.finding.file,
            a.finding.line,
        )
    )
    if len(kept) <= max_findings:
        return kept, 0
    return kept[:max_findings], len(kept) - max_findings

"""GitLab Code Quality report artifact.

GitLab reads a subset of the Code Climate schema: a flat array of issues with a
`fingerprint`, a `severity` from its own five-value scale, and a location. When the
target branch has a report too, the MR widget shows only the delta.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence

from .models import CODEQUALITY_SEVERITY, AnchoredFinding


def to_codequality(items: Sequence[AnchoredFinding]) -> List[Dict[str, Any]]:
    report: List[Dict[str, Any]] = []
    for item in items:
        finding = item.finding
        report.append(
            {
                "description": f"{finding.title} — {finding.detail.strip()}",
                "check_name": f"ai-review/{finding.category}",
                "fingerprint": finding.fingerprint(),
                "severity": CODEQUALITY_SEVERITY[finding.severity],
                "location": {
                    "path": finding.file,
                    "lines": {"begin": item.new_line or finding.line},
                },
            }
        )
    return report


def write_codequality(items: Sequence[AnchoredFinding], path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(to_codequality(items), handle, indent=2)


def write_findings_json(items: Sequence[AnchoredFinding], path: str) -> None:
    """Raw findings, kept as an artifact for debugging and prompt tuning."""
    payload = [
        {
            **item.finding.model_dump(mode="json"),
            "fingerprint": item.finding.fingerprint(),
            "anchored_new_line": item.new_line,
            "anchored_old_line": item.old_line,
        }
        for item in items
    ]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

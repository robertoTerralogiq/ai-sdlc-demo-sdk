"""Data shapes shared between the model call, the publisher and the reports."""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel

from .similarity import title_tokens


class Severity(str, Enum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"
    NIT = "nit"


# Higher number means more serious. Used by the pipeline gate.
SEVERITY_RANK = {
    Severity.NIT: 0,
    Severity.MINOR: 1,
    Severity.MAJOR: 2,
    Severity.BLOCKER: 3,
}

# GitLab Code Quality only understands its own scale.
CODEQUALITY_SEVERITY = {
    Severity.BLOCKER: "blocker",
    Severity.MAJOR: "major",
    Severity.MINOR: "minor",
    Severity.NIT: "info",
}

SEVERITY_EMOJI = {
    Severity.BLOCKER: "\N{LARGE RED CIRCLE}",
    Severity.MAJOR: "\N{LARGE ORANGE CIRCLE}",
    Severity.MINOR: "\N{LARGE YELLOW CIRCLE}",
    Severity.NIT: "\N{LARGE BLUE CIRCLE}",
}


class Finding(BaseModel):
    """One reviewer comment.

    Every field is required with no default: the schema handed to Gemini is
    generated from this class, and optional fields turn into `anyOf` unions
    that the structured-output parser handles inconsistently. The model writes
    an empty string when it has nothing to say.
    """

    file: str
    line: int
    severity: Severity
    category: str
    title: str
    detail: str
    suggestion: str
    confidence: float

    def fingerprint(self) -> str:
        """Stable id for a finding, so re-runs do not duplicate comments.

        Deliberately excludes `line` and `detail`: a rebase shifts line numbers
        and the model rewords prose between runs, but the same problem in the
        same file should still be recognised as the same problem.

        Also excludes `severity` and `category`: both are the model's
        classification of a problem, and reclassifying a problem (say, from
        major to blocker) does not make it a new problem. Only the file and a
        normalized token set of the title are hashed, so the fingerprint is
        stable across the free-text drift Gemini produces between runs.
        """
        raw = "\x00".join([self.file, " ".join(sorted(title_tokens(self.title)))])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class ReviewResponse(BaseModel):
    """Top-level structured output requested from the model."""

    findings: List[Finding]
    summary: str


class AnchoredFinding(BaseModel):
    """A finding after it has been matched against the real MR diff."""

    finding: Finding
    new_line: Optional[int] = None
    old_line: Optional[int] = None
    old_path: Optional[str] = None
    new_path: Optional[str] = None
    # True when the cited line was not itself in the diff and the position was
    # moved to a nearby added line. Such an anchor is close enough to comment on
    # but not trustworthy enough to offer as a one-click applicable suggestion.
    snapped: bool = False

    @property
    def is_anchored(self) -> bool:
        """True when the finding can be posted as an inline diff discussion."""
        return self.new_path is not None and (self.new_line is not None or self.old_line is not None)

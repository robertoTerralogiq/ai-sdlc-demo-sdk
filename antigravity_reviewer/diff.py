"""Unified-diff parsing.

The review-comments API rejects a position that does not land on a line the
diff actually touches, so the reviewer needs the real line numbers before it can
post anything inline. This module turns the raw `diff` string of each MR change
into an addressable map of lines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

ADDED = "add"
REMOVED = "del"
CONTEXT = "context"


@dataclass
class DiffLine:
    kind: str  # ADDED | REMOVED | CONTEXT
    old_line: Optional[int]
    new_line: Optional[int]
    content: str


@dataclass
class FileDiff:
    old_path: str
    new_path: str
    new_file: bool = False
    deleted_file: bool = False
    renamed_file: bool = False
    lines: List[DiffLine] = field(default_factory=list)
    truncated: bool = False

    @property
    def path(self) -> str:
        """The path a reviewer would name: the post-change one where it exists."""
        return self.new_path or self.old_path

    @property
    def added_line_numbers(self) -> List[int]:
        return [l.new_line for l in self.lines if l.kind == ADDED and l.new_line is not None]

    def render(self) -> str:
        """Re-render the diff with new-file line numbers in the margin.

        The model is asked to cite line numbers, and it is far more accurate at
        that when the numbers are printed next to the code than when it has to
        count offsets from a hunk header itself.
        """
        out: List[str] = []
        for line in self.lines:
            if line.kind == ADDED:
                marker, number = "+", line.new_line
            elif line.kind == REMOVED:
                marker, number = "-", line.old_line
            else:
                marker, number = " ", line.new_line
            gutter = str(number) if number is not None else "-"
            out.append(f"{gutter:>6} {marker}{line.content}")
        if self.truncated:
            out.append("       [diff truncated by the reviewer: file too large]")
        return "\n".join(out)


def parse_file_diff(
    raw_diff: str,
    old_path: str,
    new_path: str,
    *,
    new_file: bool = False,
    deleted_file: bool = False,
    renamed_file: bool = False,
    max_lines: int = 4000,
) -> FileDiff:
    """Parse one file's unified diff body (the part starting at `@@`)."""
    result = FileDiff(
        old_path=old_path,
        new_path=new_path,
        new_file=new_file,
        deleted_file=deleted_file,
        renamed_file=renamed_file,
    )
    old_no = new_no = 0
    for raw_line in (raw_diff or "").splitlines():
        header = HUNK_HEADER.match(raw_line)
        if header:
            old_no = int(header.group(1))
            new_no = int(header.group(3))
            continue
        if raw_line.startswith(("--- ", "+++ ", "diff --git", "index ", "old mode", "new mode")):
            continue
        if raw_line.startswith("\\"):  # "\ No newline at end of file"
            continue
        if len(result.lines) >= max_lines:
            result.truncated = True
            break

        prefix, content = (raw_line[:1], raw_line[1:]) if raw_line else (" ", "")
        if prefix == "+":
            result.lines.append(DiffLine(ADDED, None, new_no, content))
            new_no += 1
        elif prefix == "-":
            result.lines.append(DiffLine(REMOVED, old_no, None, content))
            old_no += 1
        else:
            result.lines.append(DiffLine(CONTEXT, old_no, new_no, content))
            old_no += 1
            new_no += 1
    return result


def build_line_index(diffs: Iterable[FileDiff]) -> Dict[Tuple[str, int], DiffLine]:
    """Map (path, new-file line number) -> the diff line at that position.

    Removed lines have no new-file number and are therefore not addressable by
    the model's `line` field; they still show up in the rendered diff so the
    model can reason about them, and a finding about a deletion normally lands on
    the neighbouring added or context line.
    """
    index: Dict[Tuple[str, int], DiffLine] = {}
    for file_diff in diffs:
        for line in file_diff.lines:
            if line.new_line is not None:
                index[(file_diff.path, line.new_line)] = line
    return index

"""The review prompt.

Everything the model is told lives here so the wording can be tuned without
touching the plumbing. Repo-specific guidance is appended from a
`.github/review-guidelines.md` file when the project ships one.
"""

from __future__ import annotations

from typing import List, Optional

from .diff import FileDiff

SYSTEM_INSTRUCTION = """\
You are a senior engineer reviewing a GitHub pull request. You see only the
changed hunks plus, for some files, the full post-change file for context.

Report a finding only when a reviewer would ask for a change. Specifically:

- correctness bugs, off-by-one errors, wrong conditionals, unhandled null
- crashes, unhandled exceptions, swallowed errors, empty catch blocks
- resource leaks: unclosed streams, undisposed controllers, unremoved listeners
- concurrency problems: races, unawaited futures, state mutated after teardown
- security problems: hardcoded secrets, injection, missing authorization,
  unvalidated input crossing a trust boundary, unsafe deserialization
- API or data-contract breakage that callers or persisted data depend on
- missing test coverage for logic that is easy to get wrong
- performance work in a hot path where the cost is concrete, not theoretical

Do not report: formatting, import order, naming preferences, anything a linter or
formatter already enforces, praise, restatements of what the diff does, or
speculation about code you cannot see.

Rules for every finding:

1. `file` must be exactly one of the paths given in the input, copied verbatim.
2. `line` must be a line number printed in that file's left margin. Prefer a
   line marked `+`. Never invent a number and never cite a line from a
   different file.
3. `severity`: blocker (must not merge), major (should fix before merge), minor
   (worth fixing), nit (optional polish). Be strict about blocker: reserve it
   for data loss, security holes, crashes on a normal path, or breaking changes.
4. `category`: one short lowercase word, for example correctness, security,
   performance, error-handling, concurrency, testing, api-contract, resource-leak.
5. `title`: one line under 80 characters, stating the problem.
6. `detail`: two to four sentences. Say what breaks, under what input or state,
   and what the consequence is. Cite the identifiers involved.
7. `suggestion`: the corrected code as a bare fragment with no fence and no
   commentary, ready to drop in place of the cited lines. Use an empty string
   when a code fix would be guesswork.
8. `confidence`: 0.0 to 1.0, how sure you are the problem is real given only
   what you can see. Use below 0.6 when the answer depends on code that was not
   shown to you; those findings are filtered out rather than posted.

If the change is clean, return an empty findings list and say so in `summary`.
Never pad the list to look thorough. Being wrong costs the team more than being
quiet.
"""


def build_review_prompt(
    *,
    mr_title: str,
    mr_description: str,
    target_branch: str,
    files: List[FileDiff],
    file_contexts: Optional[dict] = None,
    extra_guidelines: Optional[str] = None,
) -> str:
    """Assemble the user turn for one batch of files."""
    parts: List[str] = []
    parts.append("# Merge request")
    parts.append(f"Title: {mr_title}")
    parts.append(f"Target branch: {target_branch}")
    if mr_description.strip():
        description = mr_description.strip()
        if len(description) > 4000:
            description = description[:4000] + "\n[description truncated]"
        parts.append(f"Description:\n{description}")

    if extra_guidelines:
        parts.append("\n# Project review guidelines\n" + extra_guidelines.strip())

    parts.append("\n# Changed files")
    for file_diff in files:
        kind = "new file" if file_diff.new_file else ("renamed" if file_diff.renamed_file else "modified")
        parts.append(f"\n## {file_diff.path} ({kind})")
        if file_diff.renamed_file:
            parts.append(f"Renamed from {file_diff.old_path}")
        parts.append(
            "Diff, with post-change line numbers in the left margin "
            "(`+` added, `-` removed, blank unchanged):"
        )
        parts.append("```diff\n" + file_diff.render() + "\n```")

        context = (file_contexts or {}).get(file_diff.path)
        if context:
            parts.append(f"Full current content of {file_diff.path} for context:")
            parts.append("```\n" + context + "\n```")

    parts.append(
        "\nReview the changes above. Cite only line numbers shown in the margins "
        "of the file you name."
    )
    return "\n".join(parts)

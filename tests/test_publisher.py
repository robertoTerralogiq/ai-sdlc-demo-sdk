import json

import pytest

from antigravity_reviewer.anchor import anchor_findings
from antigravity_reviewer.codequality import to_codequality, write_codequality
from antigravity_reviewer.diff import parse_file_diff
from antigravity_reviewer.models import Finding, Severity
from antigravity_reviewer.publisher import (
    SUMMARY_MARKER,
    Publisher,
    build_summary,
    existing_fingerprints,
    fingerprint_marker,
    format_finding_body,
)

DIFF_REFS = {"base_sha": "aaa", "start_sha": "bbb", "head_sha": "ccc"}

SAMPLE = """@@ -1,4 +1,6 @@
 def f(x):
-    return x
+    if x is None:
+        return 0
+    return x + 1

 def g():
"""


class FakeClient:
    """Stands in for GitHubClient; records what would have been sent."""

    def __init__(
        self,
        bodies=None,
        existing_summary=None,
        reject_inline=False,
        comments=None,
        list_comments_fails=False,
    ):
        self.bodies = list(bodies or [])
        # Each entry: {"body": ..., "new_path": ..., "new_line": ...}. When not
        # given explicitly, synthesize bare comments (no position) from `bodies`
        # so existing tests that only set `bodies` keep working unchanged.
        self.comments = comments if comments is not None else [
            {"body": body, "new_path": None, "new_line": None} for body in self.bodies
        ]
        self.existing_summary = existing_summary
        self.reject_inline = reject_inline
        self.list_comments_fails = list_comments_fails
        self.inline = []
        self.created_notes = []
        self.updated_notes = []

    def existing_note_bodies(self):
        return [c["body"] for c in self.existing_comments()]

    def existing_comments(self):
        if self.list_comments_fails:
            raise RuntimeError("403: insufficient_granular_scope")
        return self.comments

    def create_inline_discussion(self, body, position):
        if self.reject_inline:
            raise RuntimeError("400 Bad Request: position is invalid")
        self.inline.append((body, position))

    def find_note_by_marker(self, marker):
        return self.existing_summary if marker in (self.existing_summary or "") else None

    def create_note(self, body):
        self.created_notes.append(body)

    def update_note(self, note, body):
        self.updated_notes.append(body)


def make_items(*findings):
    return anchor_findings(
        list(findings), [parse_file_diff(SAMPLE, old_path="app/f.py", new_path="app/f.py")]
    )


def finding(**kwargs):
    base = dict(
        file="app/f.py",
        line=3,
        severity=Severity.BLOCKER,
        category="correctness",
        title="Silently returns 0 for None input",
        detail="Callers cannot tell a real zero from a missing value.",
        suggestion="raise ValueError('x is required')",
        confidence=0.92,
    )
    base.update(kwargs)
    return Finding(**base)


def test_inline_body_carries_a_fingerprint_and_a_suggestion_block():
    [item] = make_items(finding())
    body = format_finding_body(item, include_suggestion=True)
    assert fingerprint_marker(item.finding.fingerprint()) in body
    assert "```suggestion\n" in body
    assert "raise ValueError" in body


def test_unanchored_body_uses_a_plain_code_block():
    [item] = make_items(finding(line=900))
    body = format_finding_body(item, include_suggestion=False)
    assert "```suggestion" not in body
    assert "Suggested change:" in body


def test_snapped_anchor_gets_no_applicable_suggestion():
    client = FakeClient()
    # Line 9 is outside the diff, so the position snaps to the nearest added line.
    [item] = make_items(finding(line=9))
    assert item.snapped is True
    Publisher(client, DIFF_REFS).publish_inline([item])
    body, _ = client.inline[0]
    assert "```suggestion" not in body
    assert "cited line 9, pinned to the nearest changed line" in body


def test_position_omits_old_line_for_added_lines():
    [item] = make_items(finding(line=3))
    client = FakeClient()
    Publisher(client, DIFF_REFS).publish_inline([item])
    _, position = client.inline[0]
    assert position["new_line"] == 3
    assert "old_line" not in position
    assert position["head_sha"] == "ccc"
    assert position["position_type"] == "text"


def test_already_posted_findings_are_skipped_on_rerun():
    [item] = make_items(finding())
    prior = f"{fingerprint_marker(item.finding.fingerprint())}\nold comment"
    client = FakeClient(bodies=[prior])
    report = Publisher(client, DIFF_REFS).publish_inline([item])
    assert report.skipped_existing == 1
    assert client.inline == []


def test_near_duplicate_of_an_existing_comment_is_skipped():
    [item] = make_items(finding(title="Silently returns zero rather than raising"))
    prior_body = (
        f"{fingerprint_marker('deadbeefdeadbeef')}\n"
        "\N{LARGE RED CIRCLE} **blocker** · `correctness` — Returns zero instead of raising\n"
        "\n"
        "old detail"
    )
    client = FakeClient(comments=[{"body": prior_body, "new_path": "app/f.py", "new_line": 3}])
    report = Publisher(client, DIFF_REFS).publish_inline([item])
    assert report.skipped_existing == 1
    assert client.inline == []


def test_failure_to_list_existing_comments_logs_and_degrades_to_posting():
    [item] = make_items(finding())
    client = FakeClient(list_comments_fails=True)
    report = Publisher(client, DIFF_REFS).publish_inline([item])
    assert report.posted_inline == 1
    assert len(client.inline) == 1


def test_rejected_position_falls_back_to_the_summary():
    [item] = make_items(finding())
    client = FakeClient(reject_inline=True)
    report = Publisher(client, DIFF_REFS).publish_inline([item])
    assert report.posted_inline == 0
    assert report.failed == ["app/f.py:3"]
    assert report.unanchored == [item]


def test_unanchored_findings_are_not_posted_inline():
    [item] = make_items(finding(line=900))
    client = FakeClient()
    report = Publisher(client, DIFF_REFS).publish_inline([item])
    assert client.inline == []
    assert report.unanchored == [item]


def test_dry_run_posts_nothing():
    [item] = make_items(finding())
    client = FakeClient()
    report = Publisher(client, DIFF_REFS, dry_run=True).publish_inline([item])
    assert report.posted_inline == 1
    assert client.inline == []


def test_summary_note_is_updated_not_duplicated():
    client = FakeClient(existing_summary=f"{SUMMARY_MARKER}\nprevious verdict")
    Publisher(client, DIFF_REFS).publish_summary(f"{SUMMARY_MARKER}\nnew verdict")
    assert client.created_notes == []
    assert client.updated_notes == [f"{SUMMARY_MARKER}\nnew verdict"]


def test_summary_note_is_created_when_absent():
    client = FakeClient()
    Publisher(client, DIFF_REFS).publish_summary("body")
    assert client.created_notes == ["body"]


def test_existing_fingerprints_reads_every_marker():
    bodies = [fingerprint_marker("abc123"), "no marker", fingerprint_marker("def456")]
    assert existing_fingerprints(bodies) == {"abc123", "def456"}


def summary_for(items, **overrides):
    kwargs = dict(
        items=items,
        unanchored=[],
        model_summaries=["Two real problems in the null path."],
        reviewed_files=["app/f.py"],
        skipped_paths=["pubspec.lock (excluded)"],
        dropped_by_cap=0,
        failed_batches=0,
        gate="blocker",
        gate_tripped=True,
        model="gemini-2.5-pro",
    )
    kwargs.update(overrides)
    return build_summary(**kwargs)


def test_summary_reports_the_verdict_and_scope():
    body = summary_for(make_items(finding()))
    assert body.startswith(SUMMARY_MARKER)
    assert "Changes requested" in body
    assert "`app/f.py:3`" in body
    assert "pubspec.lock (excluded)" in body
    assert "gemini-2.5-pro" in body


def test_summary_says_so_when_nothing_was_found():
    body = summary_for([], gate_tripped=False)
    assert "No blocking issues found" in body


def test_summary_discloses_truncation_and_failed_batches():
    body = summary_for(make_items(finding()), dropped_by_cap=4, failed_batches=2)
    assert "4 lower-severity finding(s) omitted" in body
    assert "2 batch(es) failed" in body


def test_summary_escapes_pipes_in_titles():
    body = summary_for(make_items(finding(title="a | b")))
    assert "a \\| b" in body


def test_codequality_maps_severity_and_location():
    items = make_items(finding(), finding(title="Nit", severity=Severity.NIT, line=4))
    report = to_codequality(items)
    assert [entry["severity"] for entry in report] == ["blocker", "info"]
    assert report[0]["location"] == {"path": "app/f.py", "lines": {"begin": 3}}
    assert report[0]["check_name"] == "ai-review/correctness"
    assert report[0]["fingerprint"] == items[0].finding.fingerprint()


def test_codequality_artifact_is_valid_json(tmp_path):
    path = tmp_path / "gl-code-quality-report.json"
    write_codequality(make_items(finding()), str(path))
    assert len(json.loads(path.read_text())) == 1


def test_empty_codequality_artifact_is_an_empty_array(tmp_path):
    path = tmp_path / "empty.json"
    write_codequality([], str(path))
    assert json.loads(path.read_text()) == []


@pytest.mark.parametrize(
    "severity,expected",
    [(Severity.BLOCKER, True), (Severity.MAJOR, False), (Severity.NIT, False)],
)
def test_gate_only_trips_at_or_above_the_threshold(severity, expected):
    from antigravity_reviewer.cli import gate_tripped

    assert gate_tripped(make_items(finding(severity=severity)), "blocker") is expected


def test_gate_disabled_never_trips():
    from antigravity_reviewer.cli import gate_tripped

    assert gate_tripped(make_items(finding()), None) is False


class BrokenNotesClient(FakeClient):
    """A token that may comment but may not read the thread, or may do neither."""

    def __init__(self, create_fails=False):
        super().__init__()
        self.create_fails = create_fails

    def find_note_by_marker(self, marker):
        raise RuntimeError("403: insufficient_granular_scope")

    def create_note(self, body):
        if self.create_fails:
            raise RuntimeError("403: insufficient_granular_scope")
        super().create_note(body)


def test_summary_failure_does_not_raise_after_inline_comments_posted():
    # Regression: an unhandled error here killed a run whose inline comments
    # had already landed, so the job reported failure with no verdict.
    client = BrokenNotesClient(create_fails=True)
    assert Publisher(client, DIFF_REFS).publish_summary("body") is False


def test_summary_still_posts_when_the_note_lookup_is_forbidden():
    # Listing notes needs a wider token scope than creating one. A token that can
    # comment but not read the thread should still get its verdict posted.
    client = BrokenNotesClient()
    assert Publisher(client, DIFF_REFS).publish_summary("body") is True
    assert client.created_notes == ["body"]


def test_resolve_stale_keeps_reported_threads_and_resolves_the_rest():
    from antigravity_reviewer.models import AnchoredFinding
    from antigravity_reviewer.publisher import Publisher, format_finding_body

    def anchored(f):
        return AnchoredFinding(finding=f, new_line=f.line, new_path=f.file, old_path=f.file)

    still = anchored(finding(title="SQL injection in find", line=12))
    gone = anchored(finding(title="Hardcoded fallback key", line=11))

    class Threads:
        def __init__(self):
            self.resolved = []

        def unresolved_bot_threads(self):
            return [
                {"id": "T1", "body": format_finding_body(still, include_suggestion=False),
                 "new_path": still.new_path, "new_line": 12},
                {"id": "T2", "body": format_finding_body(gone, include_suggestion=False),
                 "new_path": gone.new_path, "new_line": 11},
                {"id": "T3", "body": "a human comment", "new_path": "x", "new_line": 1},
            ]

        def resolve_thread(self, thread_id, reply):
            self.resolved.append((thread_id, reply))

    client = Threads()
    assert Publisher(client, {}).resolve_stale([still], "abcdef123") == 1
    assert [t for t, _ in client.resolved] == ["T2"]
    assert "abcdef1" in client.resolved[0][1]

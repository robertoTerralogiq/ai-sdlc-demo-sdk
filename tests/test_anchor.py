from antigravity_reviewer.anchor import anchor_findings, dedupe, filter_and_rank
from antigravity_reviewer.diff import parse_file_diff
from antigravity_reviewer.models import Finding, Severity

SAMPLE = """@@ -1,4 +1,6 @@
 def f(x):
-    return x
+    if x is None:
+        return 0
+    return x + 1

 def g():
"""


def file_diff():
    return parse_file_diff(SAMPLE, old_path="app/f.py", new_path="app/f.py")


def make_finding(**kwargs):
    base = dict(
        file="app/f.py",
        line=3,
        severity=Severity.MAJOR,
        category="correctness",
        title="Returns zero instead of raising",
        detail="detail",
        suggestion="",
        confidence=0.9,
    )
    base.update(kwargs)
    return Finding(**base)


def test_added_line_anchors_on_new_line_only():
    [item] = anchor_findings([make_finding(line=3)], [file_diff()])
    assert item.is_anchored
    assert item.new_line == 3
    assert item.old_line is None


def test_context_line_anchors_on_both_sides():
    [item] = anchor_findings([make_finding(line=6)], [file_diff()])
    assert (item.new_line, item.old_line) == (6, 4)


def test_near_miss_snaps_to_the_closest_added_line():
    # Line 8 is past the end of the diff; the nearest added line is 4.
    [item] = anchor_findings([make_finding(line=8)], [file_diff()])
    assert item.new_line == 4
    assert item.old_line is None
    assert item.snapped is True


def test_exact_anchors_are_not_marked_snapped():
    for line in (3, 6):
        [item] = anchor_findings([make_finding(line=line)], [file_diff()])
        assert item.snapped is False


def test_far_miss_stays_unanchored_instead_of_posting_a_bad_position():
    [item] = anchor_findings([make_finding(line=900)], [file_diff()])
    assert item.is_anchored is False
    assert item.finding.line == 900


def test_finding_for_an_unknown_file_is_dropped():
    assert anchor_findings([make_finding(file="other/x.py")], [file_diff()]) == []


def test_fingerprint_ignores_line_and_wording_changes():
    a = make_finding(line=3, detail="one")
    b = make_finding(line=41, detail="two")
    assert a.fingerprint() == b.fingerprint()
    assert make_finding(title="Something else").fingerprint() != a.fingerprint()


def test_fingerprint_ignores_reclassification_and_word_order():
    a = make_finding(severity=Severity.MAJOR, category="correctness")
    reclassified = make_finding(severity=Severity.BLOCKER, category="security")
    reworded = make_finding(title="Raising instead of zero returns")
    different_problem = make_finding(title="Leaks a stack trace to the client")

    assert a.fingerprint() == reclassified.fingerprint()
    assert a.fingerprint() == reworded.fingerprint()
    assert a.fingerprint() != different_problem.fingerprint()


def test_dedupe_collapses_repeats_within_a_run():
    items = anchor_findings([make_finding(), make_finding(line=4)], [file_diff()])
    assert len(items) == 2
    assert len(dedupe(items)) == 1


def test_dedupe_collapses_near_duplicate_titles_at_adjacent_lines():
    items = anchor_findings(
        [
            make_finding(line=3, title="Returns zero instead of raising"),
            make_finding(line=4, title="Silently returns zero rather than raising"),
        ],
        [file_diff()],
    )
    assert len(items) == 2
    deduped = dedupe(items)
    assert len(deduped) == 1
    assert deduped[0].finding.title == "Returns zero instead of raising"


def test_filter_drops_low_confidence():
    items = anchor_findings(
        [make_finding(confidence=0.9), make_finding(title="Shaky", confidence=0.3)],
        [file_diff()],
    )
    kept, dropped = filter_and_rank(items, min_confidence=0.6, max_findings=10)
    assert [k.finding.title for k in kept] == ["Returns zero instead of raising"]
    assert dropped == 0


def test_rank_puts_blockers_first_and_reports_the_cap():
    items = anchor_findings(
        [
            make_finding(title="minor thing", severity=Severity.MINOR),
            make_finding(title="blocking thing", severity=Severity.BLOCKER),
            make_finding(title="major thing", severity=Severity.MAJOR),
        ],
        [file_diff()],
    )
    kept, dropped = filter_and_rank(items, min_confidence=0.0, max_findings=2)
    assert [k.finding.severity for k in kept] == [Severity.BLOCKER, Severity.MAJOR]
    assert dropped == 1

from antigravity_reviewer.diff import ADDED, CONTEXT, REMOVED, build_line_index, parse_file_diff

SAMPLE = """@@ -1,4 +1,6 @@
 def f(x):
-    return x
+    if x is None:
+        return 0
+    return x + 1

 def g():
"""


def parse_sample(**kwargs):
    return parse_file_diff(SAMPLE, old_path="app/f.py", new_path="app/f.py", **kwargs)


def test_line_numbers_track_both_sides():
    result = parse_sample()
    kinds = [(l.kind, l.old_line, l.new_line) for l in result.lines]
    assert kinds == [
        (CONTEXT, 1, 1),
        (REMOVED, 2, None),
        (ADDED, None, 2),
        (ADDED, None, 3),
        (ADDED, None, 4),
        (CONTEXT, 3, 5),
        (CONTEXT, 4, 6),
    ]


def test_added_line_numbers():
    assert parse_sample().added_line_numbers == [2, 3, 4]


def test_render_puts_new_line_numbers_in_the_gutter():
    rendered = parse_sample().render()
    assert "     2 +    if x is None:" in rendered
    # A removed line is shown with its old number, so the model can still see it.
    assert "     2 -    return x" in rendered


def test_render_marks_truncation():
    result = parse_file_diff(SAMPLE, old_path="a", new_path="a", max_lines=2)
    assert result.truncated is True
    assert "[diff truncated" in result.render()
    assert len(result.lines) == 2


def test_git_headers_and_no_newline_marker_are_ignored():
    raw = (
        "diff --git a/x.py b/x.py\n"
        "index 111..222 100644\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "\\ No newline at end of file\n"
    )
    result = parse_file_diff(raw, old_path="x.py", new_path="x.py")
    assert [l.kind for l in result.lines] == [REMOVED, ADDED]


def test_multiple_hunks_reset_the_counters():
    raw = "@@ -1,2 +1,2 @@\n a\n+b\n@@ -50,2 +60,2 @@\n c\n+d\n"
    result = parse_file_diff(raw, old_path="m.py", new_path="m.py")
    assert result.added_line_numbers == [2, 61]


def test_build_line_index_keys_on_path_and_new_line():
    index = build_line_index([parse_sample()])
    assert index[("app/f.py", 2)].kind == ADDED
    assert index[("app/f.py", 6)].kind == CONTEXT
    # Removed lines have no new-file position.
    assert ("app/f.py", 7) not in index

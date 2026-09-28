import json

import httpx

from antigravity_reviewer.config import Settings
from antigravity_reviewer.github_client import GitHubClient

PATCH = "@@ -1,2 +1,3 @@\n a = 1\n-b = 2\n+b = 3\n+c = 4"


def make_client(files, review_comments=(), issue_comments=()):
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        path, page = request.url.path, int(request.url.params.get("page", 1))
        if request.method != "GET":
            sent.append((request.method, path, json.loads(request.content)))
            return httpx.Response(201, json={})
        if path.endswith("/pulls/7"):
            return httpx.Response(200, json={
                "number": 7, "title": "LOAN-12", "body": "ticket", "html_url": "u",
                "user": {"login": "dev"},
                "head": {"ref": "feat/x", "sha": "HEAD"}, "base": {"ref": "main", "sha": "BASE"},
            })
        if path.endswith("/pulls/7/files"):
            return httpx.Response(200, json=files[(page - 1) * 100: page * 100])
        if path.endswith("/pulls/7/comments"):
            return httpx.Response(200, json=list(review_comments))
        if path.endswith("/issues/7/comments"):
            return httpx.Response(200, json=list(issue_comments))
        return httpx.Response(404)

    settings = Settings(api_url="https://api.github.com", api_token="t",
                        repo="o/r", pr_number=7, api_key="k")
    return GitHubClient(settings, transport=httpx.MockTransport(handler)), sent


def test_loads_all_pages_and_skips_unreviewable_files():
    files = [{"filename": f"src/f{i}.py", "status": "modified", "patch": PATCH} for i in range(100)]
    files += [
        {"filename": "src/last.py", "status": "added", "patch": PATCH},
        {"filename": "gone.py", "status": "removed", "patch": "@@ -1 +0,0 @@\n-x"},
        {"filename": "go.sum", "status": "modified", "patch": PATCH},
        {"filename": "logo.bin", "status": "modified"},
    ]
    client, _ = make_client(files)
    client.settings.max_files = 200
    client.settings.exclude_globs.append("go.sum")
    pr = client.load_merge_request()

    assert len(pr.files) == 101 and pr.files[-1].new_file
    assert pr.diff_refs["head_sha"] == "HEAD" and pr.target_branch == "main"
    assert [p.split(" ")[0] for p in pr.skipped_paths] == ["gone.py", "go.sum", "logo.bin"]


def test_inline_comment_translates_position_to_github_line_and_side():
    client, sent = make_client([])
    client.create_inline_discussion("new", {"head_sha": "HEAD", "new_path": "a.py", "new_line": 3, "old_path": "a.py"})
    client.create_inline_discussion("old", {"head_sha": "HEAD", "new_path": "a.py", "old_path": "a.py", "old_line": 2})
    assert sent[0][2] == {"body": "new", "commit_id": "HEAD", "path": "a.py", "line": 3, "side": "RIGHT"}
    assert sent[1][2]["side"] == "LEFT" and sent[1][2]["line"] == 2


def test_summary_is_found_and_edited_in_place():
    client, sent = make_client([], issue_comments=[{"id": 55, "body": "<!-- m --> old"}])
    handle = client.find_note_by_marker("<!-- m -->")
    client.update_note(handle, "new")
    assert handle == 55 and sent == [("PATCH", "/repos/o/r/issues/comments/55", {"body": "new"})]


def test_existing_comments_merge_review_and_conversation_comments():
    client, _ = make_client(
        [],
        review_comments=[{"body": "fp", "path": "a.py", "line": 3, "side": "RIGHT"}],
        issue_comments=[{"id": 1, "body": "summary"}],
    )
    assert client.existing_comments() == [
        {"body": "fp", "new_path": "a.py", "new_line": 3},
        {"body": "summary", "new_path": None, "new_line": None},
    ]


def test_set_status_posts_on_the_reviewed_commit():
    client, sent = make_client([])
    client.set_status("HEAD", "failure", "3 finding(s)", "stage-2: ai-review (ide)")
    assert sent == [("POST", "/repos/o/r/statuses/HEAD",
                     {"state": "failure", "context": "stage-2: ai-review (ide)", "description": "3 finding(s)"})]

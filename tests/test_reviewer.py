import json

import pytest

from antigravity_reviewer.config import Settings
from antigravity_reviewer.diff import parse_file_diff
from antigravity_reviewer.reviewer import GeminiReviewer, batch_files


def settings(**overrides):
    base = dict(
        api_url="https://api.github.example.com",
        api_token="t",
        repo="1",
        pr_number=7,
        api_key="k",
        concurrency=1,
        include_file_context=False,
    )
    base.update(overrides)
    return Settings(**base)


def file_diff(path, added_lines=3):
    body = "@@ -1,1 +1,%d @@\n unchanged\n" % (added_lines + 1)
    body += "".join(f"+line {i}\n" for i in range(added_lines))
    return parse_file_diff(body, old_path=path, new_path=path)


class FakeResponse:
    def __init__(self, payload):
        self.parsed = None
        self.text = json.dumps(payload)
        self.candidates = []


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.prompts = []

    def generate_content(self, model, contents, config):
        self.prompts.append(contents)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)


class FakeGenAI:
    def __init__(self, outcomes):
        self.models = FakeModels(outcomes)


def payload(*findings, summary="ok"):
    return {"findings": list(findings), "summary": summary}


def raw_finding(**kwargs):
    base = dict(
        file="a.py",
        line=2,
        severity="major",
        category="correctness",
        title="t",
        detail="d",
        suggestion="",
        confidence=0.8,
    )
    base.update(kwargs)
    return base


def review(client, files, **setting_overrides):
    reviewer = GeminiReviewer(settings(**setting_overrides), client=client)
    return reviewer.review(
        mr_title="title",
        mr_description="",
        target_branch="main",
        files=files,
    )


def test_batching_respects_the_character_budget():
    files = [file_diff(f"f{i}.py", added_lines=20) for i in range(6)]
    single = len(files[0].render()) + len(files[0].path) + 64
    batches = batch_files(files, max_chars=single * 2 + 10)
    assert [len(b) for b in batches] == [2, 2, 2]


def test_an_oversized_file_still_gets_its_own_batch():
    files = [file_diff("huge.py", added_lines=200)]
    assert len(batch_files(files, max_chars=10)) == 1


def test_no_files_means_no_model_call():
    client = FakeGenAI([])
    outcome = review(client, [])
    assert outcome.batches == 0
    assert client.models.prompts == []


def test_findings_outside_the_batch_are_discarded():
    # The model sometimes attributes a finding to a file it saw in another batch.
    client = FakeGenAI([payload(raw_finding(file="a.py"), raw_finding(file="elsewhere.py"))])
    outcome = review(client, [file_diff("a.py")])
    assert [f.file for f in outcome.findings] == ["a.py"]


def test_prompt_includes_the_numbered_diff():
    client = FakeGenAI([payload()])
    review(client, [file_diff("a.py")])
    prompt = client.models.prompts[0]
    assert "## a.py" in prompt
    assert "+line 0" in prompt


def test_retryable_error_is_retried(monkeypatch):
    monkeypatch.setattr("antigravity_reviewer.reviewer.time.sleep", lambda _: None)
    client = FakeGenAI([RuntimeError("503 Service Unavailable"), payload(raw_finding())])
    outcome = review(client, [file_diff("a.py")])
    assert outcome.failed_batches == 0
    assert len(outcome.findings) == 1


def test_non_retryable_error_fails_only_its_own_batch():
    client = FakeGenAI([RuntimeError("400 invalid api key"), payload(raw_finding(file="b.py"))])
    outcome = review(
        client,
        [file_diff("a.py"), file_diff("b.py")],
        max_chars_per_request=1,  # forces one batch per file
    )
    assert outcome.failed_batches == 1
    assert [f.file for f in outcome.findings] == ["b.py"]


def test_parse_accepts_a_fenced_json_response():
    class Fenced:
        parsed = None
        text = '```json\n{"findings": [], "summary": "clean"}\n```'
        candidates = []

    assert GeminiReviewer._parse(Fenced()).summary == "clean"


def test_parse_raises_on_an_empty_response():
    class Empty:
        parsed = None
        text = ""
        candidates = []

    with pytest.raises(RuntimeError, match="no content"):
        GeminiReviewer._parse(Empty())


def test_client_timeout_is_retried():
    from antigravity_reviewer.reviewer import _is_retryable

    class ReadTimeout(Exception):
        pass

    assert _is_retryable(ReadTimeout("The read operation timed out"))

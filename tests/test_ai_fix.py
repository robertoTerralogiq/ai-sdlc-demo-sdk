import subprocess

from ci import ai_fix


def test_only_major_and_above_most_severe_first():
    fs = [{"severity": "minor"}, {"severity": "major"}, {"severity": "blocker"}]
    assert [f["severity"] for f in ai_fix.findings_to_fix(fs, "major")] == ["blocker", "major"]


def test_paths_outside_allowed_prefixes_are_flagged():
    assert ai_fix.disallowed(["services/a.py", ".github/workflows/ci.yml", "ci/ai_fix.py"],
                             ["services/"]) == [".github/workflows/ci.yml", "ci/ai_fix.py"]


def test_rounds_are_counted_from_commit_trailers(tmp_path, monkeypatch):
    def g(*a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=tmp_path, check=True,
                       capture_output=True)
    g("init", "-q", "-b", "main")
    g("commit", "-q", "--allow-empty", "-m", "base")
    g("switch", "-q", "-c", "feat")
    g("commit", "-q", "--allow-empty", "-m", "stage 2: implement")
    g("commit", "-q", "--allow-empty", "-m", "stage 3: ai-fix round 1", "-m", "AI-Fix-Round: 1")
    monkeypatch.chdir(tmp_path)
    assert ai_fix.rounds_done("main") == 1


def test_changed_paths_keeps_every_character_and_lists_new_files(tmp_path, monkeypatch):
    # Regression: stripping `git status --porcelain` ate the first path's leading "s".
    def g(*a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=tmp_path, check=True,
                       capture_output=True)
    (tmp_path / "services").mkdir()
    (tmp_path / "services/a.py").write_text("x = 1\n")
    g("init", "-q", "-b", "main")
    g("add", "-A")
    g("commit", "-q", "-m", "base")
    (tmp_path / "services/a.py").write_text("x = 2\n")
    (tmp_path / "services/new_test.py").write_text("")
    monkeypatch.chdir(tmp_path)
    assert ai_fix.changed_paths() == ["services/a.py", "services/new_test.py"]

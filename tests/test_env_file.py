import os

import pytest

from antigravity_reviewer.cli import load_env_file, parse_args
from antigravity_reviewer.config import ConfigError

VARS = ("GITHUB_API_URL", "GITHUB_REPOSITORY", "GITHUB_TOKEN", "GEMINI_API_KEY", "REVIEW_FAIL_ON")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in VARS:
        monkeypatch.delenv(name, raising=False)


def write_env(tmp_path, body):
    path = tmp_path / ".env"
    path.write_text(body, encoding="utf-8")
    return path


def test_values_are_loaded_from_an_explicit_path(tmp_path):
    path = write_env(tmp_path, "GITHUB_REPOSITORY=99\nREVIEW_FAIL_ON=major\n")
    loaded = load_env_file(parse_args(["--env-file", str(path)]))
    assert loaded == str(path)
    assert os.environ["GITHUB_REPOSITORY"] == "99"
    assert os.environ["REVIEW_FAIL_ON"] == "major"


def test_the_real_environment_wins_over_the_file(tmp_path, monkeypatch):
    # This is the CI case: predefined and masked variables must beat a committed .env.
    monkeypatch.setenv("GITHUB_REPOSITORY", "42")
    path = write_env(tmp_path, "GITHUB_REPOSITORY=99\n")
    load_env_file(parse_args(["--env-file", str(path)]))
    assert os.environ["GITHUB_REPOSITORY"] == "42"


def test_nearest_file_is_found_from_the_working_directory(tmp_path, monkeypatch):
    write_env(tmp_path, "GITHUB_REPOSITORY=77\n")
    nested = tmp_path / "sub" / "deeper"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert load_env_file(parse_args([])) is not None
    assert os.environ["GITHUB_REPOSITORY"] == "77"


def test_no_env_file_flag_skips_loading(tmp_path, monkeypatch):
    write_env(tmp_path, "GITHUB_REPOSITORY=77\n")
    monkeypatch.chdir(tmp_path)
    assert load_env_file(parse_args(["--no-env-file"])) is None
    assert "GITHUB_REPOSITORY" not in os.environ


def test_missing_file_is_fine_when_it_was_not_asked_for(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_env_file(parse_args([])) is None


def test_missing_explicit_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load_env_file(parse_args(["--env-file", str(tmp_path / "nope.env")]))

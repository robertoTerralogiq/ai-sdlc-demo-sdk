import pytest

from antigravity_reviewer.config import ConfigError, Settings

CI_ENV = {
    "GITHUB_API_URL": "https://ghe.example.com/api/v3/",
    "GITHUB_TOKEN": "ghs-x",
    "GITHUB_REPOSITORY": "acme-finance/loan-service",
    "REVIEW_PR_NUMBER": "7",
    "GEMINI_API_KEY": "key",
}


@pytest.fixture
def ci_env(monkeypatch):
    for key in list(CI_ENV) + [
        "REVIEW_TOKEN",
        "GOOGLE_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "REVIEW_FAIL_ON",
        "REVIEW_EXCLUDE_GLOBS",
        "REVIEW_MAX_FILES",
        "GEMINI_MODEL",
    ]:
        monkeypatch.delenv(key, raising=False)
    for key, value in CI_ENV.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def test_reads_github_actions_variables(ci_env):
    settings = Settings.from_env()
    assert settings.api_url == "https://ghe.example.com/api/v3"  # trailing slash stripped
    assert (settings.repo, settings.pr_number) == ("acme-finance/loan-service", 7)
    assert settings.api_token == "ghs-x"
    assert settings.fail_on == "blocker"


def test_api_url_defaults_to_github_dot_com(ci_env):
    ci_env.delenv("GITHUB_API_URL")
    assert Settings.from_env().api_url == "https://api.github.com"


def test_review_token_overrides_github_token(ci_env):
    ci_env.setenv("REVIEW_TOKEN", "bot")
    assert Settings.from_env().api_token == "bot"


def test_missing_pr_number_is_a_config_error(ci_env):
    ci_env.delenv("REVIEW_PR_NUMBER")
    with pytest.raises(ConfigError, match="REVIEW_PR_NUMBER"):
        Settings.from_env()


def test_missing_api_key_is_a_config_error(ci_env):
    ci_env.delenv("GEMINI_API_KEY")
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        Settings.from_env()


def test_vertex_mode_does_not_need_an_api_key(ci_env):
    ci_env.delenv("GEMINI_API_KEY")
    ci_env.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    assert Settings.from_env().use_vertex is True


def test_fail_on_never_disables_the_gate(ci_env):
    ci_env.setenv("REVIEW_FAIL_ON", "never")
    assert Settings.from_env().fail_on is None


def test_non_numeric_int_variable_is_a_config_error(ci_env):
    ci_env.setenv("REVIEW_MAX_FILES", "lots")
    with pytest.raises(ConfigError, match="REVIEW_MAX_FILES"):
        Settings.from_env()


def test_extra_exclude_globs_are_appended(ci_env):
    ci_env.setenv("REVIEW_EXCLUDE_GLOBS", "docs/**,*.golden")
    settings = Settings.from_env()
    assert settings.is_excluded("docs/api/index.md")
    assert settings.is_excluded("test/data/x.golden")


@pytest.mark.parametrize(
    "path",
    [
        "pubspec.lock",
        "lib/models/user.g.dart",
        "lib/models/user.freezed.dart",
        "web/assets/logo.png",
        "packages/app/node_modules/x/index.js",
        "android/build/out.txt",
    ],
)
def test_generated_and_binary_paths_are_excluded(ci_env, path):
    assert Settings.from_env().is_excluded(path) is True


@pytest.mark.parametrize("path", ["lib/main.dart", "app/service.py", "README.md"])
def test_source_paths_are_reviewed(ci_env, path):
    assert Settings.from_env().is_excluded(path) is False


def test_pr_flag_satisfies_the_required_number_for_a_local_run(ci_env):
    # Regression: the flag used to be applied after from_env(), so a local run with
    # no REVIEW_PR_NUMBER failed the required-variable check before it was read.
    ci_env.delenv("REVIEW_PR_NUMBER")
    assert Settings.from_env(pr_number=7).pr_number == 7


def test_pr_flag_overrides_the_ci_variable(ci_env):
    assert Settings.from_env(pr_number=99).pr_number == 99

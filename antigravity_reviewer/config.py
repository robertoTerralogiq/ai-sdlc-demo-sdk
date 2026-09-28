"""Configuration, read from GitHub Actions variables plus a few of our own."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from typing import List, Optional

# Default model. Override with GEMINI_MODEL. Gemini 3 Pro is the model behind
# Antigravity; confirm the exact id your key has access to before pinning it.
DEFAULT_MODEL = "gemini-2.5-pro"

# Paths that are never worth a model call: generated, vendored or binary.
DEFAULT_EXCLUDE = [
    "*.lock",
    "*.lockb",
    "pubspec.lock",
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "Podfile.lock",
    "*.g.dart",
    "*.freezed.dart",
    "*.gr.dart",
    "*.pb.go",
    "*_pb2.py",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.snap",
    "**/generated/**",
    "**/vendor/**",
    "**/node_modules/**",
    "**/build/**",
    "**/.dart_tool/**",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.svg",
    "*.pdf",
    "*.ttf",
    "*.otf",
    "*.woff",
    "*.woff2",
    "*.ico",
    "*.jar",
    "*.zip",
]


class ConfigError(RuntimeError):
    """Raised when a required variable is missing, before any API call."""


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _env_list(name: str) -> List[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Settings:
    # --- GitHub ---
    api_url: str
    api_token: str
    repo: str
    pr_number: int

    # --- Model ---
    model: str = DEFAULT_MODEL
    api_key: Optional[str] = None
    use_vertex: bool = False
    vertex_project: Optional[str] = None
    vertex_location: str = "us-central1"
    temperature: float = 0.2
    thinking_budget: int = -1  # -1 lets the model decide how much to think

    # --- Behaviour ---
    post_inline: bool = True
    post_summary: bool = True
    resolve_stale: bool = True
    fail_on: Optional[str] = "blocker"  # blocker | major | minor | nit | never
    min_confidence: float = 0.6
    max_findings: int = 25
    dry_run: bool = False

    # --- Budget ---
    max_files: int = 40
    max_diff_lines_per_file: int = 1500
    max_chars_per_request: int = 120_000
    context_file_max_chars: int = 40_000
    include_file_context: bool = True
    concurrency: int = 4

    exclude_globs: List[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    codequality_path: str = "gl-code-quality-report.json"
    findings_json_path: str = "ai-review-findings.json"

    @classmethod
    def from_env(cls, pr_number: Optional[int] = None) -> "Settings":
        """Build settings from the environment.

        `pr_number` carries the --pr flag, which has to be known here rather than
        applied afterwards: without it a local run fails the required-variable
        check before the override ever gets a chance to satisfy it.
        """
        url = os.environ.get("GITHUB_API_URL") or "https://api.github.com"
        # In Actions the built-in GITHUB_TOKEN is enough (pull-requests: write).
        # REVIEW_TOKEN lets a local run or a dedicated bot identity take over.
        token = os.environ.get("REVIEW_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        repo = os.environ.get("GITHUB_REPOSITORY") or ""
        pr_raw = os.environ.get("REVIEW_PR_NUMBER") or ""

        missing = []
        if not token:
            missing.append("GITHUB_TOKEN (or REVIEW_TOKEN) with pull-requests: write")
        if not repo:
            missing.append("GITHUB_REPOSITORY (owner/name)")
        if pr_number is None and not pr_raw:
            missing.append(
                "REVIEW_PR_NUMBER (set it from github.event.pull_request.number, "
                "or pass --pr for a local run)"
            )
        if missing:
            raise ConfigError("missing required configuration: " + "; ".join(missing))

        use_vertex = _env_bool("GOOGLE_GENAI_USE_VERTEXAI", False)
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not use_vertex and not api_key:
            raise ConfigError(
                "missing GEMINI_API_KEY (or set GOOGLE_GENAI_USE_VERTEXAI=true for Vertex auth)"
            )

        fail_on = (os.environ.get("REVIEW_FAIL_ON") or "blocker").strip().lower()
        if fail_on in {"never", "none", ""}:
            fail_on = None

        exclude = list(DEFAULT_EXCLUDE) + _env_list("REVIEW_EXCLUDE_GLOBS")

        return cls(
            api_url=url.rstrip("/"),
            api_token=token,
            repo=repo,
            pr_number=pr_number if pr_number is not None else int(pr_raw),
            model=os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL,
            api_key=api_key,
            use_vertex=use_vertex,
            vertex_project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            vertex_location=os.environ.get("GOOGLE_CLOUD_LOCATION") or "us-central1",
            temperature=float(os.environ.get("REVIEW_TEMPERATURE") or 0.2),
            thinking_budget=_env_int("REVIEW_THINKING_BUDGET", -1),
            post_inline=_env_bool("REVIEW_POST_INLINE", True),
            post_summary=_env_bool("REVIEW_POST_SUMMARY", True),
            resolve_stale=_env_bool("REVIEW_RESOLVE_STALE", True),
            fail_on=fail_on,
            min_confidence=float(os.environ.get("REVIEW_MIN_CONFIDENCE") or 0.6),
            max_findings=_env_int("REVIEW_MAX_FINDINGS", 25),
            dry_run=_env_bool("REVIEW_DRY_RUN", False),
            max_files=_env_int("REVIEW_MAX_FILES", 40),
            max_diff_lines_per_file=_env_int("REVIEW_MAX_DIFF_LINES", 1500),
            max_chars_per_request=_env_int("REVIEW_MAX_CHARS_PER_REQUEST", 120_000),
            context_file_max_chars=_env_int("REVIEW_CONTEXT_FILE_MAX_CHARS", 40_000),
            include_file_context=_env_bool("REVIEW_INCLUDE_FILE_CONTEXT", True),
            concurrency=_env_int("REVIEW_CONCURRENCY", 4),
            exclude_globs=exclude,
        )

    def is_excluded(self, path: str) -> bool:
        """True when a path matches any exclude glob.

        Both the bare pattern and a `**/`-prefixed form are tried so that
        `*.g.dart` matches `lib/models/user.g.dart` without the caller having to
        write the prefix.
        """
        for pattern in self.exclude_globs:
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, f"**/{pattern}"):
                return True
            if fnmatch.fnmatch(f"/{path}", f"/{pattern.lstrip('/')}"):
                return True
        return False

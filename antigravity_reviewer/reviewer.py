"""The model call: batching, structured output, retries."""

from __future__ import annotations

import json
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from google import genai
from google.genai import types

from .config import Settings
from .diff import FileDiff
from .models import Finding, ReviewResponse
from .prompts import SYSTEM_INSTRUCTION, build_review_prompt

log = logging.getLogger(__name__)

RETRYABLE_MARKERS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "resource_exhausted",
    "unavailable",
    "deadline",
    "timeout",
    "internal error",
)


# Without a client timeout a stalled response hangs until the CI job is killed.
REQUEST_TIMEOUT_MS = 180_000


@dataclass
class ReviewOutcome:
    findings: List[Finding]
    summaries: List[str]
    batches: int
    failed_batches: int


def build_client(settings: Settings) -> genai.Client:
    if settings.use_vertex:
        if not settings.vertex_project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT is required when using Vertex AI")
        return genai.Client(
            vertexai=True,
            project=settings.vertex_project,
            location=settings.vertex_location,
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        )
    return genai.Client(api_key=settings.api_key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))


def batch_files(files: Sequence[FileDiff], max_chars: int) -> List[List[FileDiff]]:
    """Group files into requests that stay under a character budget.

    A file whose own diff exceeds the budget still gets its own batch: the diff
    was already capped at REVIEW_MAX_DIFF_LINES, so sending it alone is the
    honest option rather than dropping it.
    """
    batches: List[List[FileDiff]] = []
    current: List[FileDiff] = []
    current_size = 0
    for file_diff in files:
        size = len(file_diff.render()) + len(file_diff.path) + 64
        if current and current_size + size > max_chars:
            batches.append(current)
            current, current_size = [], 0
        current.append(file_diff)
        current_size += size
    if current:
        batches.append(current)
    return batches


class GeminiReviewer:
    def __init__(
        self,
        settings: Settings,
        client: Optional[genai.Client] = None,
        guidelines: Optional[str] = None,
    ):
        self.settings = settings
        self.client = client or build_client(settings)
        self.guidelines = guidelines

    def review(
        self,
        *,
        mr_title: str,
        mr_description: str,
        target_branch: str,
        files: Sequence[FileDiff],
        context_provider: Optional[Callable[[str], Optional[str]]] = None,
    ) -> ReviewOutcome:
        batches = batch_files(files, self.settings.max_chars_per_request)
        if not batches:
            return ReviewOutcome(findings=[], summaries=[], batches=0, failed_batches=0)

        log.info("reviewing %d file(s) in %d batch(es)", len(files), len(batches))

        def run(batch: List[FileDiff]) -> Optional[ReviewResponse]:
            contexts = self._collect_contexts(batch, context_provider)
            prompt = build_review_prompt(
                mr_title=mr_title,
                mr_description=mr_description,
                target_branch=target_branch,
                files=batch,
                file_contexts=contexts,
                extra_guidelines=self.guidelines,
            )
            allowed = {f.path for f in batch}
            try:
                response = self._generate(prompt)
            except Exception as exc:  # noqa: BLE001 - one bad batch must not lose the others
                log.error("batch failed after retries (%s): %s", [f.path for f in batch], exc)
                return None
            # The model occasionally attributes a finding to a file from another
            # batch it saw earlier in the same MR; drop those here.
            response.findings = [f for f in response.findings if f.file in allowed]
            return response

        workers = max(1, min(self.settings.concurrency, len(batches)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(run, batches))

        findings: List[Finding] = []
        summaries: List[str] = []
        failed = 0
        for result in results:
            if result is None:
                failed += 1
                continue
            findings.extend(result.findings)
            if result.summary.strip():
                summaries.append(result.summary.strip())

        return ReviewOutcome(
            findings=findings,
            summaries=summaries,
            batches=len(batches),
            failed_batches=failed,
        )

    def _collect_contexts(
        self,
        batch: Sequence[FileDiff],
        context_provider: Optional[Callable[[str], Optional[str]]],
    ) -> Dict[str, str]:
        if not (self.settings.include_file_context and context_provider):
            return {}
        contexts: Dict[str, str] = {}
        for file_diff in batch:
            if file_diff.new_file:
                # A new file's diff already is the whole file.
                continue
            content = context_provider(file_diff.path)
            if content and len(content) <= self.settings.context_file_max_chars:
                contexts[file_diff.path] = content
        return contexts

    def _generate(self, prompt: str, attempts: int = 4) -> ReviewResponse:
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=self.settings.temperature,
            response_mime_type="application/json",
            response_schema=ReviewResponse,
        )
        if self.settings.thinking_budget >= 0:
            config.thinking_config = types.ThinkingConfig(
                thinking_budget=self.settings.thinking_budget
            )

        last_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.settings.model,
                    contents=prompt,
                    config=config,
                )
                return self._parse(response)
            except Exception as exc:  # noqa: BLE001 - classified below
                last_error = exc
                if attempt == attempts or not _is_retryable(exc):
                    raise
                delay = min(2 ** attempt, 16) + random.uniform(0, 1)
                log.warning("model call failed (%s), retrying in %.1fs", exc, delay)
                time.sleep(delay)
        raise RuntimeError(f"model call failed: {last_error}")

    @staticmethod
    def _parse(response: object) -> ReviewResponse:
        """Prefer the SDK's parsed object, fall back to the raw JSON text."""
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, ReviewResponse):
            return parsed
        if isinstance(parsed, dict):
            return ReviewResponse.model_validate(parsed)

        text = (getattr(response, "text", None) or "").strip()
        if not text:
            raise RuntimeError(
                "model returned no content "
                f"(finish reason: {_finish_reason(response)}); check safety filters and token limits"
            )
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[1] if "\n" in text else text
        return ReviewResponse.model_validate(json.loads(text))


def _finish_reason(response: object) -> str:
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        return str(getattr(candidates[0], "finish_reason", "unknown"))
    return "unknown"


def _is_retryable(exc: Exception) -> bool:
    message = f"{type(exc).__name__} {exc}".lower()
    return any(marker in message for marker in RETRYABLE_MARKERS)

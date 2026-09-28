"""Thin wrapper over the GitHub REST API, holding every GitHub detail in one place.

Kept narrow on purpose: the reviewer and publisher talk to this interface, so the
tests can substitute a fake without touching the network. Findings go out as pull
request review comments; the summary is a single issue comment edited in place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional

import httpx

from .config import Settings
from .diff import FileDiff, parse_file_diff

log = logging.getLogger(__name__)


@dataclass
class MergeRequestContext:
    iid: int
    title: str
    description: str
    source_branch: str
    target_branch: str
    web_url: str
    author: str
    diff_refs: Dict[str, str]
    files: List[FileDiff]
    skipped_paths: List[str]


class GitHubClient:
    def __init__(self, settings: Settings, transport: Optional[httpx.BaseTransport] = None):
        self.settings = settings
        self._http = httpx.Client(
            base_url=f"{settings.api_url}/repos/{settings.repo}",
            headers={
                "Authorization": f"Bearer {settings.api_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
            transport=transport,
        )
        self._pr = settings.pr_number

    def _get(self, path: str, **kwargs) -> Any:
        resp = self._http.get(path, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _pages(self, path: str) -> Iterator[Dict[str, Any]]:
        page = 1
        while True:
            batch = self._get(path, params={"per_page": 100, "page": page})
            yield from batch
            if len(batch) < 100:
                return
            page += 1

    # --- reading ---------------------------------------------------------

    def load_merge_request(self) -> MergeRequestContext:
        pr = self._get(f"/pulls/{self._pr}")
        files: List[FileDiff] = []
        skipped: List[str] = []
        for change in self._pages(f"/pulls/{self._pr}/files"):
            path = change["filename"]
            status = change.get("status")
            if status == "removed":
                skipped.append(f"{path} (deleted)")
                continue
            if self.settings.is_excluded(path):
                skipped.append(f"{path} (excluded)")
                continue
            # GitHub omits `patch` for binary files and for diffs it considers too large.
            if not change.get("patch"):
                skipped.append(f"{path} (binary or diff too large)")
                continue
            if len(files) >= self.settings.max_files:
                skipped.append(f"{path} (over REVIEW_MAX_FILES)")
                continue
            files.append(
                parse_file_diff(
                    change["patch"],
                    old_path=change.get("previous_filename") or path,
                    new_path=path,
                    new_file=status == "added",
                    renamed_file=status == "renamed",
                    max_lines=self.settings.max_diff_lines_per_file,
                )
            )

        return MergeRequestContext(
            iid=pr["number"],
            title=pr.get("title") or "",
            description=pr.get("body") or "",
            source_branch=pr["head"]["ref"],
            target_branch=pr["base"]["ref"],
            web_url=pr.get("html_url", ""),
            author=(pr.get("user") or {}).get("login", ""),
            diff_refs={
                "base_sha": pr["base"]["sha"],
                "start_sha": pr["base"]["sha"],
                "head_sha": pr["head"]["sha"],
            },
            files=files,
            skipped_paths=skipped,
        )

    def file_content(self, path: str, ref: str) -> Optional[str]:
        """Full post-change file text, used to give the model surrounding context."""
        resp = self._http.get(
            f"/contents/{path}", params={"ref": ref},
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        if resp.status_code != 200:
            log.debug("could not read %s@%s: HTTP %s", path, ref, resp.status_code)
            return None
        try:
            return resp.content.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def existing_comments(self) -> List[Dict[str, Any]]:
        """Review comments with their line, plus conversation comments without one."""
        comments = [
            {
                "body": c.get("body") or "",
                "new_path": c.get("path"),
                "new_line": c.get("line") if c.get("side", "RIGHT") == "RIGHT" else None,
            }
            for c in self._pages(f"/pulls/{self._pr}/comments")
        ]
        comments += [
            {"body": c.get("body") or "", "new_path": None, "new_line": None}
            for c in self._pages(f"/issues/{self._pr}/comments")
        ]
        return [c for c in comments if c["body"]]

    def existing_note_bodies(self) -> List[str]:
        return [comment["body"] for comment in self.existing_comments()]

    def find_note_by_marker(self, marker: str) -> Optional[Any]:
        """Locate the previous summary comment so it can be edited in place."""
        try:
            for c in self._pages(f"/issues/{self._pr}/comments"):
                if marker in (c.get("body") or ""):
                    return c["id"]
        except httpx.HTTPError as exc:
            log.warning("cannot list PR comments (%s); the summary will be posted as new", exc)
        return None

    def _graphql(self, query: str, **variables) -> Dict[str, Any]:
        base = self.settings.api_url
        url = base[: -len("/v3")] + "/graphql" if base.endswith("/api/v3") else base + "/graphql"
        resp = self._http.post(url, json={"query": query, "variables": variables})
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors"):
            raise RuntimeError(data["errors"][0].get("message", "GraphQL error"))
        return data["data"]

    def _bot_threads(self) -> tuple[str, List[Dict[str, Any]]]:
        """Our review threads (first comment carries the fingerprint marker), plus who we are."""
        owner, name = self.settings.repo.split("/", 1)
        data = self._graphql(
            """query($owner: String!, $name: String!, $pr: Int!) {
              viewer { login }
              repository(owner: $owner, name: $name) { pullRequest(number: $pr) {
                reviewThreads(first: 100) { nodes {
                  id isResolved resolvedBy { login } path line
                  comments(first: 1) { nodes { body } } } } } } }""",
            owner=owner, name=name, pr=self._pr,
        )
        threads = []
        for node in data["repository"]["pullRequest"]["reviewThreads"]["nodes"]:
            body = (node["comments"]["nodes"] or [{}])[0].get("body") or ""
            if "antigravity-reviewer:fp=" in body:
                # `line` is null once the commented code changed (an outdated thread).
                threads.append({"id": node["id"], "body": body, "new_path": node["path"],
                                "new_line": node["line"], "resolved": node["isResolved"],
                                "resolved_by": (node.get("resolvedBy") or {}).get("login")})
        return data["viewer"]["login"], threads

    def unresolved_bot_threads(self) -> List[Dict[str, Any]]:
        return [t for t in self._bot_threads()[1] if not t["resolved"]]

    def human_resolved_threads(self) -> List[Dict[str, Any]]:
        """Our threads that someone other than the reviewer resolved: findings a person accepted."""
        me, threads = self._bot_threads()
        return [t for t in threads if t["resolved"] and t["resolved_by"] not in (None, me)]

    # --- writing ---------------------------------------------------------

    def set_status(self, sha: str, state: str, description: str, context: str) -> None:
        """Commit status on the reviewed commit, so branch protection can require it."""
        self._http.post(f"/statuses/{sha}", json={
            "state": state, "context": context, "description": description[:140],
        }).raise_for_status()

    def resolve_thread(self, thread_id: str, reply: str) -> None:
        self._graphql(
            """mutation($id: ID!, $body: String!) {
              addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: $id, body: $body}) { clientMutationId }
              resolveReviewThread(input: {threadId: $id}) { clientMutationId } }""",
            id=thread_id, body=reply,
        )

    def create_inline_discussion(self, body: str, position: Dict[str, Any]) -> None:
        """Post a review comment. `position` is the publisher's shape, translated here."""
        on_new_side = position.get("new_line") is not None
        payload = {
            "body": body,
            "commit_id": position["head_sha"],
            "path": position["new_path"] if on_new_side else position["old_path"],
            "line": position["new_line"] if on_new_side else position["old_line"],
            "side": "RIGHT" if on_new_side else "LEFT",
        }
        self._http.post(f"/pulls/{self._pr}/comments", json=payload).raise_for_status()

    def create_note(self, body: str) -> None:
        self._http.post(f"/issues/{self._pr}/comments", json={"body": body}).raise_for_status()

    def update_note(self, handle: Any, body: str) -> None:
        self._http.patch(f"/issues/comments/{handle}", json={"body": body}).raise_for_status()

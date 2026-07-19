from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from time import sleep
from typing import Any

import httpx

from repopulse._timeutils import ensure_utc, to_iso_z


class GitHubAPIError(RuntimeError):
    """Raised when GitHub returns a response that the pipeline cannot recover from."""


class GitHubRateLimitError(GitHubAPIError):
    """Raised when GitHub asks the client to wait before sending more requests."""


class GitHubNotFoundError(GitHubAPIError):
    """Raised when a GitHub resource disappears between related API requests."""


@dataclass(frozen=True)
class PaginationStats:
    pages_fetched: int = 0
    items_fetched: int = 0
    truncated: bool = False


class GitHubClient:
    BASE_URL = "https://api.github.com"

    def __init__(
        self,
        token: str | None = None,
        *,
        max_pages: int = 10,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        retry_attempts: int = 3,
        retry_backoff: float = 0.25,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "RepoPulse/0.2",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.max_pages = max_pages
        self.retry_attempts = max(1, retry_attempts)
        self.retry_backoff = max(0.0, retry_backoff)
        self.pagination_stats: dict[str, PaginationStats] = {}
        self._client = httpx.Client(
            base_url=self.BASE_URL,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        response: httpx.Response | None = None
        for attempt in range(self.retry_attempts):
            try:
                response = self._client.get(path, params=params)
            except httpx.TransportError as exc:
                if attempt + 1 >= self.retry_attempts:
                    raise GitHubAPIError(f"GitHub API 网络错误: {exc}") from exc
                sleep(self.retry_backoff * (2**attempt))
                continue
            if response.status_code >= 500 and attempt + 1 < self.retry_attempts:
                sleep(self.retry_backoff * (2**attempt))
                continue
            break
        if response is None:
            raise GitHubAPIError("GitHub API 请求未返回响应")
        if response.status_code in {403, 429}:
            reset = response.headers.get("x-ratelimit-reset")
            retry_after = response.headers.get("retry-after")
            detail = f"retry-after={retry_after}" if retry_after else f"reset={reset}"
            raise GitHubRateLimitError(f"GitHub API 触发限流（{detail}）")
        if response.status_code == 404:
            message = response.text[:300]
            raise GitHubNotFoundError(f"GitHub API 404: {message}")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            message = response.text[:300]
            raise GitHubAPIError(f"GitHub API {response.status_code}: {message}") from exc
        return response

    def _paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        coverage_key: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        query = dict(params or {})
        query["per_page"] = 100
        page = 1
        pages_fetched = 0
        items_fetched = 0
        truncated = False
        try:
            while page <= self.max_pages:
                query["page"] = page
                payload = self._get(path, params=query).json()
                if not isinstance(payload, list):
                    raise GitHubAPIError(f"分页接口返回了非列表数据: {path}")
                pages_fetched += 1
                items_fetched += len(payload)
                yield from payload
                if len(payload) < query["per_page"]:
                    break
                if page == self.max_pages:
                    truncated = True
                    break
                page += 1
        finally:
            if coverage_key:
                previous = self.pagination_stats.get(coverage_key, PaginationStats())
                self.pagination_stats[coverage_key] = PaginationStats(
                    pages_fetched=previous.pages_fetched + pages_fetched,
                    items_fetched=previous.items_fetched + items_fetched,
                    truncated=previous.truncated or truncated,
                )

    def get_repository(self, repository: str) -> dict[str, Any]:
        return self._get(f"/repos/{repository}").json()

    def get_issues(self, repository: str, *, since: datetime | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"state": "all", "sort": "updated", "direction": "desc"}
        if since:
            params["since"] = _github_timestamp(since)
        # GitHub's issues endpoint also returns pull requests. Keep only true issues.
        return [
            item
            for item in self._paginate(
                f"/repos/{repository}/issues", params=params, coverage_key="issues"
            )
            if "pull_request" not in item
        ]

    def get_pull_requests(
        self, repository: str, *, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        params = {"state": "all", "sort": "updated", "direction": "desc"}
        items: list[dict[str, Any]] = []
        for item in self._paginate(
            f"/repos/{repository}/pulls",
            params=params,
            coverage_key="pull_requests",
        ):
            updated_at = _parse_timestamp(item.get("updated_at"))
            if since and updated_at and updated_at < ensure_utc(since):
                break
            items.append(item)
        return items

    def get_commits(
        self, repository: str, *, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if since:
            params["since"] = _github_timestamp(since)
        return list(
            self._paginate(f"/repos/{repository}/commits", params=params, coverage_key="commits")
        )

    def get_releases(self, repository: str) -> list[dict[str, Any]]:
        return list(self._paginate(f"/repos/{repository}/releases", coverage_key="releases"))

    def get_issue_comments(
        self, repository: str, *, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Repository-level issue comments (one endpoint, cheap to paginate).

        GitHub does not return the issue number on each comment, so we synthesize
        it from ``issue_url`` to make the events table joinable to ``issues``.
        """
        params: dict[str, Any] = {"sort": "created", "direction": "desc"}
        if since:
            params["since"] = _github_timestamp(since)
        comments: list[dict[str, Any]] = []
        for item in self._paginate(
            f"/repos/{repository}/issues/comments",
            params=params,
            coverage_key="issue_comments",
        ):
            issue_url = item.get("issue_url") or ""
            number = _issue_number_from_url(issue_url)
            if number is None:
                continue
            comments.append({**item, "issue_number": number})
        return comments

    def get_pr_reviews(self, repository: str, pr_number: int) -> list[dict[str, Any]]:
        """Reviews for a single PR. There is no repository-level reviews endpoint,
        so callers must loop PRs themselves; that loop is intentionally kept in
        the pipeline so it can apply a window-bounded strategy.
        """
        reviews: list[dict[str, Any]] = []
        try:
            for item in self._paginate(
                f"/repos/{repository}/pulls/{pr_number}/reviews",
                coverage_key="pr_reviews",
            ):
                reviews.append({**item, "pr_number": pr_number})
        except GitHubNotFoundError:
            # A force-push, repository migration, or GitHub-side stale node can
            # make one PR disappear after it was returned by the pulls list.
            # Losing that PR's reviews should not discard the rest of a large
            # repository refresh.
            return []
        return reviews

    def rate_limit(self) -> dict[str, Any]:
        return self._get("/rate_limit").json()


def _github_timestamp(value: datetime) -> str:
    return to_iso_z(value)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = parsedate_to_datetime(value)
    return ensure_utc(parsed)


def _issue_number_from_url(url: str) -> int | None:
    """Extract the integer issue number from a GitHub issue_url like
    ``https://api.github.com/repos/owner/repo/issues/42``."""
    if not url:
        return None
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return None

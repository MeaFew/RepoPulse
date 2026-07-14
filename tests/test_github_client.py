import httpx
import pytest

from repopulse.github_client import GitHubClient, GitHubRateLimitError


def test_issue_endpoint_filters_pull_requests() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["state"] == "all"
        return httpx.Response(
            200,
            json=[
                {"number": 1, "title": "issue"},
                {"number": 2, "title": "pr", "pull_request": {"url": "example"}},
            ],
        )

    with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        issues = client.get_issues("owner/repo")

    assert [item["number"] for item in issues] == [1]


def test_rate_limit_is_reported_without_busy_retry() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"x-ratelimit-reset": "123456"}, json={})

    with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GitHubRateLimitError, match="限流"):
            client.get_repository("owner/repo")


def test_transient_server_error_is_retried() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(502, json={"message": "temporary"})
        return httpx.Response(200, json={"full_name": "owner/repo"})

    with GitHubClient(
        transport=httpx.MockTransport(handler), retry_backoff=0
    ) as client:
        repository = client.get_repository("owner/repo")

    assert repository["full_name"] == "owner/repo"
    assert attempts == 2


def test_pagination_limit_records_conservative_coverage() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"number": number, "title": "issue"} for number in range(100)],
        )

    with GitHubClient(
        max_pages=1, transport=httpx.MockTransport(handler)
    ) as client:
        issues = client.get_issues("owner/repo")
        stats = client.pagination_stats["issues"]

    assert len(issues) == 100
    assert stats.pages_fetched == 1
    assert stats.items_fetched == 100
    assert stats.truncated is True

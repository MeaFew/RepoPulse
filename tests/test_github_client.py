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

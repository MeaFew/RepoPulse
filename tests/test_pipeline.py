from __future__ import annotations

from repopulse import pipeline as pipeline_module
from repopulse.github_client import PaginationStats
from repopulse.pipeline import collect_repository
from repopulse.storage import Warehouse


class FakeGitHubClient:
    def __init__(self, *_args, **_kwargs) -> None:
        self.pagination_stats = {
            "issues": PaginationStats(1, 100, True),
            "pull_requests": PaginationStats(1, 0, False),
            "commits": PaginationStats(1, 0, False),
            "releases": PaginationStats(1, 0, False),
            "issue_comments": PaginationStats(1, 0, False),
            "pr_reviews": PaginationStats(0, 0, False),
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def get_repository(self, repository: str) -> dict:
        return {"full_name": repository}

    def get_issues(self, *_args, **_kwargs) -> list[dict]:
        return [
            {
                "number": 1,
                "state": "open",
                "user": {"login": "maintainer"},
                "title": "Needs triage",
                "labels": [],
                "comments": 0,
                "locked": False,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
                "closed_at": None,
            }
        ]

    def get_pull_requests(self, *_args, **_kwargs) -> list[dict]:
        return []

    def get_commits(self, *_args, **_kwargs) -> list[dict]:
        return []

    def get_releases(self, *_args, **_kwargs) -> list[dict]:
        return []

    def get_issue_comments(self, *_args, **_kwargs) -> list[dict]:
        return []

    def get_pr_reviews(self, *_args, **_kwargs) -> list[dict]:
        return []


def test_collection_persists_pagination_coverage(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pipeline_module, "GitHubClient", FakeGitHubClient)
    db_path = tmp_path / "pipeline.duckdb"

    result = collect_repository("owner/repo", db_path, max_pages=1)

    assert result.counts["issues"] == 1
    assert result.truncated_entities == ("issues",)
    with Warehouse(db_path) as warehouse:
        coverage = warehouse.connection.execute(
            """
            SELECT entity_type, history_complete, last_run_truncated
            FROM collection_coverage ORDER BY entity_type
            """
        ).fetchall()
        run = warehouse.connection.execute(
            "SELECT status, max_pages, is_incremental FROM pipeline_runs"
        ).fetchone()

    assert len(coverage) == 6
    assert ("issues", False, True) in coverage
    assert run == ("success", 1, False)

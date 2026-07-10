from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from repopulse.config import validate_repository
from repopulse.github_client import GitHubClient
from repopulse.storage import Warehouse


@dataclass(frozen=True)
class CollectionResult:
    repository: str
    counts: dict[str, int]
    started_at: datetime
    finished_at: datetime

    @property
    def total_loaded(self) -> int:
        return sum(self.counts.values())


def collect_repository(
    repository: str,
    db_path: str | Path,
    *,
    token: str | None = None,
    max_pages: int = 10,
) -> CollectionResult:
    """Incrementally collect one repository and upsert it into DuckDB."""
    repository = validate_repository(repository)
    run_id = str(uuid4())
    started_at = datetime.now(UTC)
    counts: dict[str, int] = {}

    with Warehouse(db_path) as warehouse:
        warehouse.initialize()
        warehouse.start_run(run_id, repository, started_at)
        try:
            # A small overlap makes updates resilient to equal timestamps and clock skew.
            issue_since = _with_overlap(warehouse.latest_timestamp(repository, "issues"))
            pr_since = _with_overlap(warehouse.latest_timestamp(repository, "pull_requests"))
            commit_since = _with_overlap(warehouse.latest_timestamp(repository, "commits"))

            with GitHubClient(token, max_pages=max_pages) as github:
                repo = github.get_repository(repository)
                issues = github.get_issues(repository, since=issue_since)
                pull_requests = github.get_pull_requests(repository, since=pr_since)
                commits = github.get_commits(repository, since=commit_since)
                releases = github.get_releases(repository)

            warehouse.upsert_repository(repo, fetched_at=datetime.now(UTC))
            counts = {
                "issues": warehouse.upsert_issues(repository, issues),
                "pull_requests": warehouse.upsert_pull_requests(repository, pull_requests),
                "commits": warehouse.upsert_commits(repository, commits),
                "releases": warehouse.upsert_releases(repository, releases),
            }
            finished_at = datetime.now(UTC)
            warehouse.finish_run(run_id, finished_at=finished_at, status="success", counts=counts)
        except Exception as exc:
            finished_at = datetime.now(UTC)
            warehouse.finish_run(
                run_id,
                finished_at=finished_at,
                status="failed",
                counts=counts,
                error_message=str(exc)[:1000],
            )
            raise

    return CollectionResult(repository, counts, started_at, finished_at)


def _with_overlap(value: datetime | None) -> datetime | None:
    return value - timedelta(minutes=5) if value else None

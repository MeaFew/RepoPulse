from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from repopulse.config import validate_repository
from repopulse.github_client import GitHubClient, PaginationStats
from repopulse.storage import Warehouse

# Only PRs created within this trailing window get their per-PR reviews fetched.
# There is no repository-level reviews endpoint, so fetching reviews for every
# historical PR would cost one API call per PR; capping to recent PRs keeps the
# first-response metric current without exploding API usage on large repos.
PR_REVIEW_WINDOW_DAYS = 180


@dataclass(frozen=True)
class CollectionResult:
    repository: str
    counts: dict[str, int]
    started_at: datetime
    finished_at: datetime
    truncated_entities: tuple[str, ...] = ()

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
        is_incremental = warehouse.repository_exists(repository)
        warehouse.start_run(
            run_id,
            repository,
            started_at,
            max_pages=max_pages,
            is_incremental=is_incremental,
        )
        try:
            # A small overlap makes updates resilient to equal timestamps and clock skew.
            issue_since = _with_overlap(warehouse.latest_timestamp(repository, "issues"))
            pr_since = _with_overlap(warehouse.latest_timestamp(repository, "pull_requests"))
            commit_since = _with_overlap(warehouse.latest_timestamp(repository, "commits"))
            comment_since = _with_overlap(
                warehouse.latest_timestamp(repository, "issue_comments")
            )

            with GitHubClient(token, max_pages=max_pages) as github:
                repo = github.get_repository(repository)
                issues = github.get_issues(repository, since=issue_since)
                pull_requests = github.get_pull_requests(repository, since=pr_since)
                commits = github.get_commits(repository, since=commit_since)
                releases = github.get_releases(repository)
                issue_comments = github.get_issue_comments(repository, since=comment_since)

                # PR reviews: only fetch for PRs created in the trailing window.
                # Reviews on ancient closed PRs add little signal and cost one
                # API call each, so they are intentionally skipped.
                review_pr_numbers = _recent_pr_numbers(
                    pull_requests, warehouse, repository, pr_since
                )
                pr_reviews: list[dict] = []
                for pr_number in review_pr_numbers:
                    pr_reviews.extend(github.get_pr_reviews(repository, pr_number))
                pagination_stats = dict(github.pagination_stats)

            warehouse.upsert_repository(repo, fetched_at=datetime.now(UTC))
            counts = {
                "issues": warehouse.upsert_issues(repository, issues),
                "pull_requests": warehouse.upsert_pull_requests(repository, pull_requests),
                "commits": warehouse.upsert_commits(repository, commits),
                "releases": warehouse.upsert_releases(repository, releases),
                "issue_comments": warehouse.upsert_issue_comments(repository, issue_comments),
                "pr_reviews": warehouse.upsert_pr_reviews(repository, pr_reviews),
            }
            finished_at = datetime.now(UTC)
            for entity_type in counts:
                stats = pagination_stats.get(entity_type, PaginationStats())
                warehouse.update_coverage(
                    repository,
                    entity_type,
                    run_id=run_id,
                    refreshed_at=finished_at,
                    pages_fetched=stats.pages_fetched,
                    max_pages=max_pages,
                    truncated=stats.truncated,
                    coverage_scope=(
                        f"trailing_{PR_REVIEW_WINDOW_DAYS}_days"
                        if entity_type == "pr_reviews"
                        else "full_history"
                    ),
                    replace_history=entity_type == "releases",
                )
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

    truncated_entities = tuple(
        sorted(entity for entity, stats in pagination_stats.items() if stats.truncated)
    )
    return CollectionResult(
        repository,
        counts,
        started_at,
        finished_at,
        truncated_entities,
    )


def _recent_pr_numbers(
    new_pull_requests: list[dict],
    warehouse: Warehouse,
    repository: str,
    pr_since: datetime | None,
) -> list[int]:
    """Return PR numbers created within the review window, from both the batch we
    just fetched and what is already in the warehouse.

    On a first run (no warehouse data) we only consider the freshly fetched PRs,
    so the cost is bounded by max_pages rather than the full PR history.
    """
    cutoff = datetime.now(UTC) - timedelta(days=PR_REVIEW_WINDOW_DAYS)
    numbers: set[int] = set()

    for item in new_pull_requests:
        created = item.get("created_at")
        if created and _coerce_utc(created) >= cutoff:
            numbers.add(item["number"])

    # If this is an incremental run, also top up with PRs already stored that
    # fall in the window but were not returned again by the since-filter.
    if pr_since is not None:
        rows = warehouse.connection.execute(
            """
            SELECT pr_number FROM pull_requests
            WHERE repo_full_name = ? AND created_at >= ?
            """,
            [repository, cutoff],
        ).fetchall()
        numbers.update(row[0] for row in rows)

    return sorted(numbers)


def _coerce_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _with_overlap(value: datetime | None) -> datetime | None:
    return value - timedelta(minutes=5) if value else None

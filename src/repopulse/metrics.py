from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

ENTITY_LABELS = {
    "issues": "Issue",
    "pull_requests": "Pull Request",
    "commits": "Commit",
    "releases": "Release",
    "issue_comments": "Issue 评论",
    "pr_reviews": "PR Review",
}


@dataclass(frozen=True)
class RiskFlag:
    level: str
    title: str
    detail: str


@dataclass(frozen=True)
class Window:
    """Half-open [start, end) interval used to scope every metric in this class.

    ``start=None`` means "no lower bound" and ``end=None`` means "up to now".
    ``Window.all()`` reproduces the pre-window behavior, which is what the
    test suite and CLI ``summary`` command rely on.

    Boundaries are resolved to sentinel timestamps in :meth:`bounds`, so callers
    can write plain ``created_at >= ? AND created_at < ?`` SQL without worrying
    about ``NULL`` comparisons swallowing every row.
    """

    start: datetime | None = None
    end: datetime | None = None

    @classmethod
    def all(cls) -> Window:
        return cls(start=None, end=None)

    def with_end_now(self) -> Window:
        """Replace a None end with the current UTC instant (used for age/backlog math)."""
        return Window(start=self.start, end=self.end or datetime.now(UTC))

    def bounds(self) -> tuple[datetime, datetime]:
        """Return (start, end) with Nones replaced by wide sentinels safe for SQL."""
        start = self.start or datetime(1970, 1, 1, tzinfo=UTC)
        end = self.end or datetime(2286, 7, 5, tzinfo=UTC)
        return start, end


class Analytics:
    """Central metric definitions used by both the UI and tests."""

    def __init__(self, db_path: str | Path) -> None:
        # DuckDB requires every connection to the same file in one process to
        # use the same configuration. Streamlit can overlap a warehouse
        # migration connection with a dashboard rerun, so a read-only analytics
        # connection would fail even though every query below is SELECT-only.
        # The online demo remains application-level read-only: it exposes no
        # mutation controls and operates on a writable copy of the snapshot.
        self.connection = duckdb.connect(str(db_path))
        self.connection.execute("SET TimeZone='UTC'")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Analytics:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def repositories(self) -> list[str]:
        rows = self.connection.execute(
            "SELECT repo_full_name FROM repositories ORDER BY repo_full_name"
        ).fetchall()
        return [row[0] for row in rows]

    def overview(self, repository: str) -> dict[str, Any]:
        # The repository snapshot is point-in-time, not a time series, so it is
        # intentionally not scoped by Window. Counts below are also kept as
        # lifetime totals to match what a GitHub project page shows.
        return self._one(
            """
            SELECT
                r.repo_full_name,
                r.description,
                r.stars,
                r.forks,
                r.language,
                r.license_name,
                r.fetched_at,
                (SELECT count(*) FROM issues i WHERE i.repo_full_name = r.repo_full_name)
                    AS issues,
                (SELECT count(*) FROM pull_requests p WHERE p.repo_full_name = r.repo_full_name)
                    AS pull_requests,
                (SELECT count(*) FROM commits c WHERE c.repo_full_name = r.repo_full_name)
                    AS commits,
                (SELECT count(*) FROM releases x WHERE x.repo_full_name = r.repo_full_name
                    AND NOT x.draft) AS releases
            FROM repositories r
            WHERE r.repo_full_name = ?
            """,
            [repository],
        )

    def issue_kpis(self, repository: str, window: Window | None = None) -> dict[str, Any]:
        window = window or Window.all()
        start, end = window.bounds()
        # created_at scopes "the population of issues we judge"; stale_open uses
        # the window end (default now) so backlog aging stays meaningful under a
        # custom date range instead of drifting against current_timestamp.
        return self._one(
            """
            SELECT
                count(*) AS total,
                count(*) FILTER (WHERE state = 'open') AS open,
                count(*) FILTER (WHERE state = 'closed') AS closed,
                round(100.0 * count(*) FILTER (WHERE state = 'closed') /
                    nullif(count(*), 0), 1) AS close_rate,
                round(median(date_diff('minute', created_at, closed_at) / 60.0)
                    FILTER (WHERE closed_at IS NOT NULL), 1) AS median_close_hours,
                round(quantile_cont(date_diff('minute', created_at, closed_at) / 60.0, 0.9)
                    FILTER (WHERE closed_at IS NOT NULL), 1) AS p90_close_hours,
                count(*) FILTER (
                    WHERE state = 'open' AND created_at < ? - INTERVAL 90 DAY
                ) AS stale_open
            FROM issues
            WHERE repo_full_name = ? AND created_at >= ? AND created_at < ?
            """,
            [window.with_end_now().end, repository, start, end],
        )

    def pr_kpis(self, repository: str, window: Window | None = None) -> dict[str, Any]:
        window = window or Window.all()
        start, end = window.bounds()
        return self._one(
            """
            SELECT
                count(*) AS total,
                count(*) FILTER (WHERE state = 'open') AS open,
                count(*) FILTER (WHERE merged_at IS NOT NULL) AS merged,
                round(100.0 * count(*) FILTER (WHERE merged_at IS NOT NULL) /
                    nullif(count(*) FILTER (WHERE NOT draft AND closed_at IS NOT NULL), 0), 1)
                    AS merge_rate,
                round(median(date_diff('minute', created_at, merged_at) / 60.0)
                    FILTER (WHERE merged_at IS NOT NULL), 1) AS median_merge_hours,
                round(quantile_cont(date_diff('minute', created_at, merged_at) / 60.0, 0.9)
                    FILTER (WHERE merged_at IS NOT NULL), 1) AS p90_merge_hours
            FROM pull_requests
            WHERE repo_full_name = ? AND created_at >= ? AND created_at < ?
            """,
            [repository, start, end],
        )

    def contributor_kpis(self, repository: str, window: Window | None = None) -> dict[str, Any]:
        window = window or Window.all()
        start, end = window.bounds()
        # active_window_end defaults to now so "active 90d" stays a trailing
        # indicator even when a custom window is selected; otherwise a 30-day
        # window would always report zero active contributors.
        active_window_end = window.end or datetime.now(UTC)
        return self._one(
            """
            WITH activity AS (
                SELECT author, committed_at AS occurred_at FROM commits
                WHERE repo_full_name = ? AND author IS NOT NULL
                  AND committed_at >= ? AND committed_at < ?
                UNION ALL
                SELECT author, created_at AS occurred_at FROM pull_requests
                WHERE repo_full_name = ? AND author IS NOT NULL
                  AND created_at >= ? AND created_at < ?
            ), counts AS (
                SELECT author, count(*) AS events FROM activity GROUP BY author
            )
            SELECT
                (SELECT count(DISTINCT author) FROM activity) AS contributors,
                (SELECT count(DISTINCT author) FROM activity
                    WHERE occurred_at >= ? - INTERVAL 90 DAY) AS active_90d,
                coalesce(round(100.0 * max(events) / nullif(sum(events), 0), 1), 0)
                    AS top_contributor_share
            FROM counts
            """,
            [
                repository,
                start,
                end,
                repository,
                start,
                end,
                active_window_end,
            ],
        )

    def monthly_activity(self, repository: str, window: Window | None = None) -> pd.DataFrame:
        window = window or Window.all()
        start, end = window.bounds()
        return self.connection.execute(
            """
            WITH activity AS (
                SELECT date_trunc('month', created_at) AS month, 'Issue' AS activity_type
                FROM issues WHERE repo_full_name = ? AND created_at >= ? AND created_at < ?
                UNION ALL
                SELECT date_trunc('month', created_at), 'PR'
                FROM pull_requests WHERE repo_full_name = ? AND created_at >= ? AND created_at < ?
                UNION ALL
                SELECT date_trunc('month', committed_at), 'Commit'
                FROM commits WHERE repo_full_name = ? AND committed_at >= ? AND committed_at < ?
            )
            SELECT month, activity_type, count(*) AS activity_count
            FROM activity
            GROUP BY month, activity_type
            ORDER BY month, activity_type
            """,
            [
                repository,
                start,
                end,
                repository,
                start,
                end,
                repository,
                start,
                end,
            ],
        ).df()

    def top_contributors(
        self, repository: str, window: Window | None = None, limit: int = 12
    ) -> pd.DataFrame:
        window = window or Window.all()
        start, end = window.bounds()
        return self.connection.execute(
            """
            WITH activity AS (
                SELECT author, 'Commit' AS activity_type FROM commits
                WHERE repo_full_name = ? AND author IS NOT NULL
                  AND committed_at >= ? AND committed_at < ?
                UNION ALL
                SELECT author, 'PR' FROM pull_requests
                WHERE repo_full_name = ? AND author IS NOT NULL
                  AND created_at >= ? AND created_at < ?
            )
            SELECT
                author,
                count(*) FILTER (WHERE activity_type = 'Commit') AS commits,
                count(*) FILTER (WHERE activity_type = 'PR') AS pull_requests,
                count(*) AS total_activity
            FROM activity
            GROUP BY author
            ORDER BY total_activity DESC, author
            LIMIT ?
            """,
            [
                repository,
                start,
                end,
                repository,
                start,
                end,
                limit,
            ],
        ).df()

    def contributor_retention(self, repository: str, window: Window | None = None) -> pd.DataFrame:
        window = window or Window.all()
        start, end = window.bounds()
        return self.connection.execute(
            """
            WITH raw_activity AS (
                SELECT author, date_trunc('month', committed_at) AS activity_month
                FROM commits WHERE repo_full_name = ? AND author IS NOT NULL
                  AND committed_at >= ? AND committed_at < ?
                UNION
                SELECT author, date_trunc('month', created_at)
                FROM pull_requests WHERE repo_full_name = ? AND author IS NOT NULL
                  AND created_at >= ? AND created_at < ?
            ), first_activity AS (
                SELECT author, min(activity_month) AS cohort_month
                FROM raw_activity GROUP BY author
            ), cohort_activity AS (
                SELECT
                    f.cohort_month,
                    date_diff('month', f.cohort_month, a.activity_month) AS month_number,
                    count(DISTINCT a.author) AS active_contributors
                FROM raw_activity a
                JOIN first_activity f USING (author)
                GROUP BY f.cohort_month, month_number
            ), cohort_sizes AS (
                SELECT cohort_month, count(*) AS cohort_size
                FROM first_activity GROUP BY cohort_month
            )
            SELECT
                c.cohort_month,
                c.month_number,
                s.cohort_size,
                c.active_contributors,
                round(100.0 * c.active_contributors / s.cohort_size, 1) AS retention_rate
            FROM cohort_activity c
            JOIN cohort_sizes s USING (cohort_month)
            WHERE c.month_number BETWEEN 0 AND 12
            ORDER BY c.cohort_month, c.month_number
            """,
            [
                repository,
                start,
                end,
                repository,
                start,
                end,
            ],
        ).df()

    def recent_runs(self, repository: str) -> pd.DataFrame:
        return self.connection.execute(
            """
            SELECT started_at, finished_at, status, issues_loaded, pull_requests_loaded,
                   commits_loaded, releases_loaded, issue_comments_loaded, pr_reviews_loaded,
                   max_pages, is_incremental, error_message
            FROM pipeline_runs
            WHERE repo_full_name = ?
            ORDER BY started_at DESC
            LIMIT 10
            """,
            [repository],
        ).df()

    def data_coverage(self, repository: str) -> pd.DataFrame:
        """Return persisted evidence about history depth and the latest refresh."""
        return self.connection.execute(
            """
            SELECT entity_type, record_count, first_observed_at, last_observed_at,
                   pages_fetched, max_pages, last_run_truncated, history_complete,
                   coverage_scope, refreshed_at
            FROM collection_coverage
            WHERE repo_full_name = ?
            ORDER BY entity_type
            """,
            [repository],
        ).df()

    def data_quality_flags(self, repository: str) -> list[RiskFlag]:
        """Explain whether the current metrics are fresh and historically complete."""
        coverage = self.data_coverage(repository)
        latest_run = self._one(
            """
            SELECT status, started_at, finished_at, max_pages, is_incremental, error_message
            FROM pipeline_runs WHERE repo_full_name = ?
            ORDER BY started_at DESC LIMIT 1
            """,
            [repository],
        )
        flags: list[RiskFlag] = []

        if latest_run.get("status") == "failed":
            detail = latest_run.get("error_message") or "未记录具体错误。"
            flags.append(RiskFlag("high", "最近采集失败", str(detail)))
        elif latest_run.get("status") == "running":
            flags.append(RiskFlag("medium", "采集尚未结束", "最近一次采集仍处于运行状态。"))

        if coverage.empty:
            flags.append(
                RiskFlag(
                    "medium",
                    "覆盖范围未知",
                    "这份数据来自旧版快照；重新采集后才能判断是否覆盖完整历史。",
                )
            )
        else:
            incomplete = coverage.loc[~coverage["history_complete"], "entity_type"].tolist()
            if incomplete:
                labels = "、".join(ENTITY_LABELS.get(item, item) for item in incomplete)
                flags.append(
                    RiskFlag(
                        "medium",
                        "历史数据可能不完整",
                        f"{labels} 曾达到分页上限；后续增量刷新不能自动补齐此前缺口。",
                    )
                )
            if "pr_reviews" in coverage["entity_type"].tolist():
                flags.append(
                    RiskFlag(
                        "info",
                        "PR Review 为窗口覆盖",
                        "PR Review 只采集最近 180 天内创建的 PR，这是明确的成本边界。",
                    )
                )

        fetched_at = self.overview(repository).get("fetched_at")
        if fetched_at is not None:
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=UTC)
            age = datetime.now(UTC) - fetched_at.astimezone(UTC)
            if age > timedelta(hours=48):
                flags.append(
                    RiskFlag(
                        "medium",
                        "数据已超过 48 小时未刷新",
                        f"仓库元数据最后采集于 {fetched_at:%Y-%m-%d %H:%M UTC}。",
                    )
                )

        if not any(flag.level in {"high", "medium"} for flag in flags):
            flags.insert(
                0,
                RiskFlag(
                    "good",
                    "核心数据可信度正常",
                    "最近采集成功，且未发现由分页上限造成的历史缺口。",
                ),
            )
        return flags

    def maintainer_tasks(
        self,
        repository: str,
        window: Window | None = None,
        limit: int = 20,
    ) -> pd.DataFrame:
        """Prioritized, directly actionable open Issue and PR follow-ups."""
        end = (window or Window.all()).with_end_now().end
        return self.connection.execute(
            """
            WITH issue_responses AS (
                SELECT DISTINCT i.issue_number
                FROM issues i
                JOIN issue_comments c
                  ON c.repo_full_name = i.repo_full_name
                 AND c.issue_number = i.issue_number
                WHERE i.repo_full_name = ?
                  AND c.author <> i.author
                  AND NOT (starts_with(c.author, 'dependabot')
                        OR starts_with(c.author, 'github-actions')
                        OR ends_with(c.author, '[bot]')
                        OR c.author = 'codecov')
            ), issue_tasks AS (
                SELECT
                    CASE
                        WHEN i.created_at < ? - INTERVAL 90 DAY THEN 3
                        WHEN r.issue_number IS NULL
                             AND i.created_at < ? - INTERVAL 30 DAY THEN 3
                        ELSE 2
                    END AS sort_rank,
                    CASE
                        WHEN i.created_at < ? - INTERVAL 90 DAY
                          OR (r.issue_number IS NULL
                              AND i.created_at < ? - INTERVAL 30 DAY) THEN '高'
                        ELSE '中'
                    END AS priority,
                    'Issue' AS task_type,
                    i.issue_number AS item_number,
                    i.title,
                    date_diff('day', i.created_at, ?) AS age_days,
                    CASE
                        WHEN i.created_at < ? - INTERVAL 90 DAY
                             AND r.issue_number IS NULL THEN '开放超过 90 天且无人响应'
                        WHEN i.created_at < ? - INTERVAL 90 DAY THEN '开放超过 90 天'
                        WHEN r.issue_number IS NULL THEN '超过 7 天无人响应'
                        ELSE '开放超过 30 天'
                    END AS reason,
                    concat('https://github.com/', i.repo_full_name, '/issues/', i.issue_number)
                        AS url
                FROM issues i
                LEFT JOIN issue_responses r USING (issue_number)
                WHERE i.repo_full_name = ? AND i.state = 'open' AND i.created_at < ?
                  AND (i.created_at < ? - INTERVAL 30 DAY
                       OR (r.issue_number IS NULL
                           AND i.created_at < ? - INTERVAL 7 DAY))
            ), reviewed_prs AS (
                SELECT DISTINCT p.pr_number
                FROM pull_requests p
                JOIN pr_reviews r
                  ON r.repo_full_name = p.repo_full_name AND r.pr_number = p.pr_number
                WHERE p.repo_full_name = ?
                  AND r.author <> p.author
                  AND NOT (starts_with(r.author, 'dependabot')
                        OR starts_with(r.author, 'github-actions')
                        OR ends_with(r.author, '[bot]')
                        OR r.author = 'codecov')
            ), pr_tasks AS (
                SELECT
                    CASE
                        WHEN p.created_at < ? - INTERVAL 90 DAY THEN 3
                        WHEN r.pr_number IS NULL
                             AND p.created_at < ? - INTERVAL 14 DAY THEN 3
                        ELSE 2
                    END AS sort_rank,
                    CASE
                        WHEN p.created_at < ? - INTERVAL 90 DAY
                          OR (r.pr_number IS NULL
                              AND p.created_at < ? - INTERVAL 14 DAY) THEN '高'
                        ELSE '中'
                    END AS priority,
                    'PR' AS task_type,
                    p.pr_number AS item_number,
                    p.title,
                    date_diff('day', p.created_at, ?) AS age_days,
                    CASE
                        WHEN r.pr_number IS NULL THEN '等待首次 Review'
                        ELSE '开放超过 30 天'
                    END AS reason,
                    concat('https://github.com/', p.repo_full_name, '/pull/', p.pr_number) AS url
                FROM pull_requests p
                LEFT JOIN reviewed_prs r USING (pr_number)
                WHERE p.repo_full_name = ? AND p.state = 'open' AND NOT p.draft
                  AND p.created_at < ?
                  AND (p.created_at < ? - INTERVAL 30 DAY
                       OR (r.pr_number IS NULL
                           AND p.created_at < ? - INTERVAL 7 DAY))
            )
            SELECT priority, task_type, item_number, title, age_days, reason, url
            FROM (
                SELECT * FROM issue_tasks
                UNION ALL
                SELECT * FROM pr_tasks
            )
            ORDER BY sort_rank DESC, age_days DESC, task_type, item_number
            LIMIT ?
            """,
            [
                repository,
                end,
                end,
                end,
                end,
                end,
                end,
                end,
                repository,
                end,
                end,
                end,
                repository,
                end,
                end,
                end,
                end,
                end,
                repository,
                end,
                end,
                end,
                limit,
            ],
        ).df()

    # --- Maintenance-efficiency metrics (events layer) ----------------------

    def issue_response_kpis(self, repository: str, window: Window | None = None) -> dict[str, Any]:
        """Time-to-first-response on issues, using the issue_comments events table.

        Excludes the issue author replying to themselves and well-known bots, so
        the median reflects a real maintainer/peer reaction. Issues with no
        qualifying response are reported separately via ``no_response`` and are
        intentionally kept out of the median (using 0 would drag it down).
        """
        window = window or Window.all()
        start, end = window.bounds()
        return self._one(
            """
            WITH responded AS (
                SELECT
                    i.issue_number,
                    i.author AS issue_author,
                    min(ic.created_at) AS first_response_at
                FROM issues i
                JOIN issue_comments ic
                  ON ic.repo_full_name = i.repo_full_name AND ic.issue_number = i.issue_number
                WHERE i.repo_full_name = ?
                  AND i.created_at >= ? AND i.created_at < ?
                  AND ic.author <> i.author
                  AND NOT (starts_with(ic.author, 'dependabot')
                        OR starts_with(ic.author, 'github-actions')
                        OR ends_with(ic.author, '[bot]')
                        OR ic.author = 'codecov')
                GROUP BY i.issue_number, i.author
            )
            SELECT
                count(*) AS responded_issues,
                coalesce(round(
                    median(
                        date_diff('minute', i.created_at, r.first_response_at) / 60.0),
                    1), NULL) AS median_first_response_hours,
                coalesce(round(
                    quantile_cont(
                        date_diff('minute', i.created_at, r.first_response_at) / 60.0, 0.9),
                    1), NULL) AS p90_first_response_hours,
                (SELECT count(*) FROM issues
                   WHERE repo_full_name = ? AND created_at >= ? AND created_at < ?) AS total_issues,
                ((SELECT count(*) FROM issues
                   WHERE repo_full_name = ? AND created_at >= ? AND created_at < ?)
                 - coalesce((SELECT count(*) FROM responded), 0)) AS no_response
            FROM responded r
            JOIN issues i ON i.repo_full_name = ? AND i.issue_number = r.issue_number
            """,
            [repository, start, end, repository, start, end, repository, start, end, repository],
        )

    def pr_response_kpis(self, repository: str, window: Window | None = None) -> dict[str, Any]:
        """Time-to-first-review on PRs, using the pr_reviews events table.

        A "review" is any non-author review event (approved, commented, or
        changes requested). Comments on PRs are not counted here because the
        events table only stores formal reviews; that keeps the metric
        comparable across projects.
        """
        window = window or Window.all()
        start, end = window.bounds()
        return self._one(
            """
            WITH reviewed AS (
                SELECT
                    p.pr_number,
                    p.author AS pr_author,
                    min(pr.submitted_at) AS first_review_at
                FROM pull_requests p
                JOIN pr_reviews pr
                  ON pr.repo_full_name = p.repo_full_name AND pr.pr_number = p.pr_number
                WHERE p.repo_full_name = ?
                  AND p.created_at >= ? AND p.created_at < ?
                  AND pr.author <> p.author
                  AND NOT (starts_with(pr.author, 'dependabot')
                        OR starts_with(pr.author, 'github-actions')
                        OR ends_with(pr.author, '[bot]')
                        OR pr.author = 'codecov')
                GROUP BY p.pr_number, p.author
            )
            SELECT
                count(*) AS reviewed_prs,
                coalesce(round(
                    median(
                        date_diff('minute', p.created_at, r.first_review_at) / 60.0),
                    1), NULL) AS median_first_review_hours,
                coalesce(round(
                    quantile_cont(
                        date_diff('minute', p.created_at, r.first_review_at) / 60.0, 0.9),
                    1), NULL) AS p90_first_review_hours,
                (SELECT count(*) FROM pull_requests
                   WHERE repo_full_name = ? AND created_at >= ? AND created_at < ?) AS total_prs,
                ((SELECT count(*) FROM pull_requests
                   WHERE repo_full_name = ? AND created_at >= ? AND created_at < ?)
                 - coalesce((SELECT count(*) FROM reviewed), 0)) AS no_review
            FROM reviewed r
            JOIN pull_requests p ON p.repo_full_name = ? AND p.pr_number = r.pr_number
            """,
            [repository, start, end, repository, start, end, repository, start, end, repository],
        )

    def backlog_kpis(self, repository: str, window: Window | None = None) -> dict[str, Any]:
        """30/90-day backlog ratios for both issues and PRs.

        Backlog is judged against the window end (default now), so picking a
        custom end date answers "what was the backlog as of that day". Issues
        and PRs live in separate tables, so they are scored independently and
        merged into one dict.
        """
        window = window or Window.all()
        end = window.with_end_now().end

        issue = self._one(
            """
            SELECT
                count(*) FILTER (WHERE state = 'open') AS open_issues,
                count(*) FILTER (WHERE state = 'open' AND created_at < ? - INTERVAL 30 DAY)
                    AS issue_stale_30,
                count(*) FILTER (WHERE state = 'open' AND created_at < ? - INTERVAL 90 DAY)
                    AS issue_stale_90
            FROM issues
            WHERE repo_full_name = ? AND created_at < ?
            """,
            [end, end, repository, end],
        )
        pr = self._one(
            """
            SELECT
                count(*) FILTER (WHERE state = 'open') AS open_prs,
                count(*) FILTER (WHERE state = 'open' AND created_at < ? - INTERVAL 30 DAY)
                    AS pr_stale_30,
                count(*) FILTER (WHERE state = 'open' AND created_at < ? - INTERVAL 90 DAY)
                    AS pr_stale_90
            FROM pull_requests
            WHERE repo_full_name = ? AND created_at < ?
            """,
            [end, end, repository, end],
        )

        open_issues = issue.get("open_issues") or 0
        issue_stale_90 = issue.get("issue_stale_90") or 0
        open_prs = pr.get("open_prs") or 0
        pr_stale_90 = pr.get("pr_stale_90") or 0
        return {
            "open_issues": open_issues,
            "issue_stale_30": issue.get("issue_stale_30") or 0,
            "issue_stale_90": issue_stale_90,
            "issue_backlog_90_pct": round(100.0 * issue_stale_90 / open_issues, 1)
            if open_issues
            else None,
            "open_prs": open_prs,
            "pr_stale_30": pr.get("pr_stale_30") or 0,
            "pr_stale_90": pr_stale_90,
            "pr_backlog_90_pct": round(100.0 * pr_stale_90 / open_prs, 1) if open_prs else None,
        }

    def comparison_kpis(
        self, repositories: list[str], window: Window | None = None
    ) -> pd.DataFrame:
        """One row per repository with the metrics used by the compare view.

        Pulls issue/PR/contributor KPIs for each repo into a single tidy frame
        so the UI can build a comparison table and grouped bar charts.
        """
        window = window or Window.all()
        rows: list[dict[str, Any]] = []
        for repo in repositories:
            issue = self.issue_kpis(repo, window)
            pr = self.pr_kpis(repo, window)
            contributor = self.contributor_kpis(repo, window)
            overview = self.overview(repo)
            rows.append(
                {
                    "repository": repo,
                    "language": overview.get("language"),
                    "stars": overview.get("stars"),
                    "issue_close_rate": issue.get("close_rate"),
                    "issue_median_close_hours": issue.get("median_close_hours"),
                    "pr_merge_rate": pr.get("merge_rate"),
                    "pr_median_merge_hours": pr.get("median_merge_hours"),
                    "active_90d": contributor.get("active_90d"),
                    "top_contributor_share": contributor.get("top_contributor_share"),
                }
            )
        return pd.DataFrame(rows)

    def risk_flags(self, repository: str, window: Window | None = None) -> list[RiskFlag]:
        issue = self.issue_kpis(repository, window)
        pr = self.pr_kpis(repository, window)
        contributor = self.contributor_kpis(repository, window)
        flags: list[RiskFlag] = []

        open_issues = issue.get("open") or 0
        stale_open = issue.get("stale_open") or 0
        stale_share = stale_open / open_issues if open_issues else 0
        if stale_share >= 0.4:
            flags.append(
                RiskFlag(
                    "high",
                    "Issue 积压老化",
                    f"{stale_open} 个开放 Issue 已超过 90 天，占开放 Issue 的 {stale_share:.0%}。",
                )
            )
        elif stale_open:
            flags.append(
                RiskFlag("medium", "存在陈旧 Issue", f"有 {stale_open} 个开放 Issue 已超过 90 天。")
            )

        concentration = contributor.get("top_contributor_share") or 0
        if concentration >= 50:
            flags.append(
                RiskFlag(
                    "high",
                    "贡献集中度偏高",
                    f"活跃度最高的贡献者贡献了 {concentration:.1f}% 的 Commit 与 PR。",
                )
            )

        merge_rate = pr.get("merge_rate")
        if merge_rate is not None and merge_rate < 40:
            flags.append(
                RiskFlag("medium", "PR 合并率偏低", f"当前非草稿 PR 合并率为 {merge_rate:.1f}% 。")
            )

        if not flags:
            flags.append(
                RiskFlag(
                    "good",
                    "未发现明显风险",
                    "当前阈值下未触发积压、集中度或合并率预警。",
                )
            )
        return flags

    def _one(self, sql: str, params: list[Any]) -> dict[str, Any]:
        cursor = self.connection.execute(sql, params)
        row = cursor.fetchone()
        if row is None:
            return {}
        columns = [column[0] for column in cursor.description]
        return dict(zip(columns, row, strict=True))

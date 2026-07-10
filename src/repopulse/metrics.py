from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd


@dataclass(frozen=True)
class RiskFlag:
    level: str
    title: str
    detail: str


class Analytics:
    """Central metric definitions used by both the UI and tests."""

    def __init__(self, db_path: str | Path) -> None:
        self.connection = duckdb.connect(str(db_path), read_only=True)
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

    def issue_kpis(self, repository: str) -> dict[str, Any]:
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
                    WHERE state = 'open' AND created_at < current_timestamp - INTERVAL 90 DAY
                ) AS stale_open
            FROM issues
            WHERE repo_full_name = ?
            """,
            [repository],
        )

    def pr_kpis(self, repository: str) -> dict[str, Any]:
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
            WHERE repo_full_name = ?
            """,
            [repository],
        )

    def contributor_kpis(self, repository: str) -> dict[str, Any]:
        return self._one(
            """
            WITH activity AS (
                SELECT author, committed_at AS occurred_at FROM commits
                WHERE repo_full_name = ? AND author IS NOT NULL
                UNION ALL
                SELECT author, created_at AS occurred_at FROM pull_requests
                WHERE repo_full_name = ? AND author IS NOT NULL
            ), counts AS (
                SELECT author, count(*) AS events FROM activity GROUP BY author
            )
            SELECT
                (SELECT count(DISTINCT author) FROM activity) AS contributors,
                (SELECT count(DISTINCT author) FROM activity
                    WHERE occurred_at >= current_timestamp - INTERVAL 90 DAY)
                    AS active_90d,
                coalesce(round(100.0 * max(events) / nullif(sum(events), 0), 1), 0)
                    AS top_contributor_share
            FROM counts
            """,
            [repository, repository],
        )

    def monthly_activity(self, repository: str) -> pd.DataFrame:
        return self.connection.execute(
            """
            WITH activity AS (
                SELECT date_trunc('month', created_at) AS month, 'Issue' AS activity_type
                FROM issues WHERE repo_full_name = ?
                UNION ALL
                SELECT date_trunc('month', created_at), 'PR'
                FROM pull_requests WHERE repo_full_name = ?
                UNION ALL
                SELECT date_trunc('month', committed_at), 'Commit'
                FROM commits WHERE repo_full_name = ?
            )
            SELECT month, activity_type, count(*) AS activity_count
            FROM activity
            GROUP BY month, activity_type
            ORDER BY month, activity_type
            """,
            [repository, repository, repository],
        ).df()

    def top_contributors(self, repository: str, limit: int = 12) -> pd.DataFrame:
        return self.connection.execute(
            """
            WITH activity AS (
                SELECT author, 'Commit' AS activity_type FROM commits
                WHERE repo_full_name = ? AND author IS NOT NULL
                UNION ALL
                SELECT author, 'PR' FROM pull_requests
                WHERE repo_full_name = ? AND author IS NOT NULL
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
            [repository, repository, limit],
        ).df()

    def contributor_retention(self, repository: str) -> pd.DataFrame:
        return self.connection.execute(
            """
            WITH raw_activity AS (
                SELECT author, date_trunc('month', committed_at) AS activity_month
                FROM commits WHERE repo_full_name = ? AND author IS NOT NULL
                UNION
                SELECT author, date_trunc('month', created_at)
                FROM pull_requests WHERE repo_full_name = ? AND author IS NOT NULL
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
            [repository, repository],
        ).df()

    def recent_runs(self, repository: str) -> pd.DataFrame:
        return self.connection.execute(
            """
            SELECT started_at, finished_at, status, issues_loaded, pull_requests_loaded,
                   commits_loaded, releases_loaded, error_message
            FROM pipeline_runs
            WHERE repo_full_name = ?
            ORDER BY started_at DESC
            LIMIT 10
            """,
            [repository],
        ).df()

    def risk_flags(self, repository: str) -> list[RiskFlag]:
        issue = self.issue_kpis(repository)
        pr = self.pr_kpis(repository)
        contributor = self.contributor_kpis(repository)
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

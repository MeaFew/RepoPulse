from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS repositories (
    repo_full_name VARCHAR PRIMARY KEY,
    description VARCHAR,
    stars INTEGER NOT NULL,
    forks INTEGER NOT NULL,
    watchers INTEGER NOT NULL,
    open_issues INTEGER NOT NULL,
    default_branch VARCHAR,
    language VARCHAR,
    license_name VARCHAR,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    pushed_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS issues (
    repo_full_name VARCHAR NOT NULL,
    issue_number INTEGER NOT NULL,
    state VARCHAR NOT NULL,
    author VARCHAR,
    title VARCHAR,
    labels_json VARCHAR,
    comments INTEGER NOT NULL,
    locked BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ,
    PRIMARY KEY (repo_full_name, issue_number)
);

CREATE TABLE IF NOT EXISTS pull_requests (
    repo_full_name VARCHAR NOT NULL,
    pr_number INTEGER NOT NULL,
    state VARCHAR NOT NULL,
    author VARCHAR,
    title VARCHAR,
    draft BOOLEAN NOT NULL,
    comments INTEGER NOT NULL,
    review_comments INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ,
    merged_at TIMESTAMPTZ,
    merge_commit_sha VARCHAR,
    PRIMARY KEY (repo_full_name, pr_number)
);

CREATE TABLE IF NOT EXISTS commits (
    repo_full_name VARCHAR NOT NULL,
    sha VARCHAR NOT NULL,
    author VARCHAR,
    message VARCHAR,
    authored_at TIMESTAMPTZ,
    committed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (repo_full_name, sha)
);

CREATE TABLE IF NOT EXISTS releases (
    repo_full_name VARCHAR NOT NULL,
    release_id BIGINT NOT NULL,
    tag_name VARCHAR,
    name VARCHAR,
    author VARCHAR,
    draft BOOLEAN NOT NULL,
    prerelease BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    PRIMARY KEY (repo_full_name, release_id)
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id VARCHAR PRIMARY KEY,
    repo_full_name VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status VARCHAR NOT NULL,
    issues_loaded INTEGER DEFAULT 0,
    pull_requests_loaded INTEGER DEFAULT 0,
    commits_loaded INTEGER DEFAULT 0,
    releases_loaded INTEGER DEFAULT 0,
    error_message VARCHAR
);
"""


class Warehouse:
    """Thin persistence layer around an embedded DuckDB analytics warehouse."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(self.db_path)
        self.connection.execute("SET TimeZone='UTC'")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Warehouse:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def initialize(self) -> None:
        self.connection.execute(SCHEMA_SQL)

    def upsert_repository(self, item: dict[str, Any], fetched_at: datetime) -> None:
        license_info = item.get("license") or {}
        self.connection.execute(
            """
            INSERT OR REPLACE INTO repositories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                item["full_name"],
                item.get("description"),
                item.get("stargazers_count", 0),
                item.get("forks_count", 0),
                item.get("subscribers_count", item.get("watchers_count", 0)),
                item.get("open_issues_count", 0),
                item.get("default_branch"),
                item.get("language"),
                license_info.get("spdx_id") or license_info.get("name"),
                item.get("created_at"),
                item.get("updated_at"),
                item.get("pushed_at"),
                fetched_at,
            ],
        )

    def upsert_issues(self, repository: str, items: Iterable[dict[str, Any]]) -> int:
        rows = []
        for item in items:
            labels = [label.get("name") for label in item.get("labels", []) if label.get("name")]
            rows.append(
                [
                    repository,
                    item["number"],
                    item["state"],
                    _login(item.get("user")),
                    item.get("title"),
                    json.dumps(labels, ensure_ascii=False),
                    item.get("comments", 0),
                    item.get("locked", False),
                    item["created_at"],
                    item["updated_at"],
                    item.get("closed_at"),
                ]
            )
        if rows:
            self.connection.executemany(
                "INSERT OR REPLACE INTO issues VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
            )
        return len(rows)

    def upsert_pull_requests(self, repository: str, items: Iterable[dict[str, Any]]) -> int:
        rows = [
            [
                repository,
                item["number"],
                item["state"],
                _login(item.get("user")),
                item.get("title"),
                item.get("draft", False),
                item.get("comments", 0),
                item.get("review_comments", 0),
                item["created_at"],
                item["updated_at"],
                item.get("closed_at"),
                item.get("merged_at"),
                item.get("merge_commit_sha"),
            ]
            for item in items
        ]
        if rows:
            self.connection.executemany(
                "INSERT OR REPLACE INTO pull_requests "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def upsert_commits(self, repository: str, items: Iterable[dict[str, Any]]) -> int:
        rows = []
        for item in items:
            commit = item.get("commit", {})
            author_meta = commit.get("author") or {}
            committer_meta = commit.get("committer") or {}
            author = _login(item.get("author")) or author_meta.get("name")
            rows.append(
                [
                    repository,
                    item["sha"],
                    author,
                    commit.get("message"),
                    author_meta.get("date"),
                    committer_meta.get("date") or author_meta.get("date"),
                ]
            )
        rows = [row for row in rows if row[-1] is not None]
        if rows:
            self.connection.executemany(
                "INSERT OR REPLACE INTO commits VALUES (?, ?, ?, ?, ?, ?)", rows
            )
        return len(rows)

    def upsert_releases(self, repository: str, items: Iterable[dict[str, Any]]) -> int:
        rows = [
            [
                repository,
                item["id"],
                item.get("tag_name"),
                item.get("name"),
                _login(item.get("author")),
                item.get("draft", False),
                item.get("prerelease", False),
                item.get("created_at"),
                item.get("published_at"),
            ]
            for item in items
        ]
        if rows:
            self.connection.executemany(
                "INSERT OR REPLACE INTO releases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
            )
        return len(rows)

    def latest_timestamp(self, repository: str, entity: str) -> datetime | None:
        mapping = {
            "issues": ("issues", "updated_at"),
            "pull_requests": ("pull_requests", "updated_at"),
            "commits": ("commits", "committed_at"),
        }
        if entity not in mapping:
            raise ValueError(f"不支持的实体类型: {entity}")
        table, column = mapping[entity]
        row = self.connection.execute(
            f"SELECT max({column}) FROM {table} WHERE repo_full_name = ?", [repository]
        ).fetchone()
        return row[0] if row else None

    def start_run(self, run_id: str, repository: str, started_at: datetime) -> None:
        self.connection.execute(
            """
            INSERT INTO pipeline_runs (run_id, repo_full_name, started_at, status)
            VALUES (?, ?, ?, 'running')
            """,
            [run_id, repository, started_at],
        )

    def finish_run(
        self,
        run_id: str,
        *,
        finished_at: datetime,
        status: str,
        counts: dict[str, int] | None = None,
        error_message: str | None = None,
    ) -> None:
        counts = counts or {}
        self.connection.execute(
            """
            UPDATE pipeline_runs
            SET finished_at = ?, status = ?, issues_loaded = ?, pull_requests_loaded = ?,
                commits_loaded = ?, releases_loaded = ?, error_message = ?
            WHERE run_id = ?
            """,
            [
                finished_at,
                status,
                counts.get("issues", 0),
                counts.get("pull_requests", 0),
                counts.get("commits", 0),
                counts.get("releases", 0),
                error_message,
                run_id,
            ],
        )


def _login(value: dict[str, Any] | None) -> str | None:
    return value.get("login") if value else None

from datetime import UTC, datetime

import duckdb

from repopulse.storage import Warehouse


def test_issue_upsert_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "test.duckdb"
    item = {
        "number": 1,
        "state": "open",
        "user": {"login": "analyst"},
        "title": "Metric definition",
        "labels": [{"name": "analytics"}],
        "comments": 2,
        "locked": False,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "closed_at": None,
    }

    with Warehouse(db_path) as warehouse:
        warehouse.initialize()
        warehouse.upsert_issues("owner/repo", [item])
        warehouse.upsert_issues("owner/repo", [item])
        count = warehouse.connection.execute("SELECT count(*) FROM issues").fetchone()[0]
        latest = warehouse.latest_timestamp("owner/repo", "issues")

    assert count == 1
    assert latest == datetime(2026, 1, 2, tzinfo=UTC)


def test_initialize_migrates_old_pipeline_runs_schema(tmp_path) -> None:
    """A database created with the original (pre-events) schema must still work
    after upgrade: initialize() backfills the columns added later instead of
    relying on CREATE TABLE IF NOT EXISTS, which is a no-op on existing tables.
    """
    db_path = tmp_path / "legacy.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(
        """
        CREATE TABLE repositories (
            repo_full_name VARCHAR PRIMARY KEY, description VARCHAR,
            stars INTEGER NOT NULL, forks INTEGER NOT NULL, watchers INTEGER NOT NULL,
            open_issues INTEGER NOT NULL, default_branch VARCHAR, language VARCHAR,
            license_name VARCHAR, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ,
            pushed_at TIMESTAMPTZ, fetched_at TIMESTAMPTZ NOT NULL
        );
        CREATE TABLE pipeline_runs (
            run_id VARCHAR PRIMARY KEY, repo_full_name VARCHAR NOT NULL,
            started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ,
            status VARCHAR NOT NULL, issues_loaded INTEGER DEFAULT 0,
            pull_requests_loaded INTEGER DEFAULT 0, commits_loaded INTEGER DEFAULT 0,
            releases_loaded INTEGER DEFAULT 0, error_message VARCHAR
        );
        INSERT INTO pipeline_runs VALUES
            ('r1','owner/repo','2024-01-01','2024-01-01','success',5,3,10,2,NULL);
        """
    )
    con.close()

    with Warehouse(db_path) as warehouse:
        warehouse.initialize()
        cols = [
            row[0]
            for row in warehouse.connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'pipeline_runs' ORDER BY ordinal_position"
            ).fetchall()
        ]
        versions = [
            row[0]
            for row in warehouse.connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]

    assert "issue_comments_loaded" in cols
    assert "pr_reviews_loaded" in cols
    assert "max_pages" in cols
    assert "is_incremental" in cols
    assert versions == [1, 2]

    from repopulse.metrics import Analytics

    with Analytics(db_path) as analytics:
        runs = analytics.recent_runs("owner/repo")

    assert len(runs) == 1
    assert runs.iloc[0]["issues_loaded"] == 5


def test_incomplete_incremental_history_stays_incomplete(tmp_path) -> None:
    db_path = tmp_path / "coverage.duckdb"
    item = {
        "number": 1,
        "state": "open",
        "user": {"login": "analyst"},
        "title": "Old issue",
        "labels": [],
        "comments": 0,
        "locked": False,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "closed_at": None,
    }
    refreshed_at = datetime(2026, 1, 1, tzinfo=UTC)

    with Warehouse(db_path) as warehouse:
        warehouse.initialize()
        warehouse.upsert_issues("owner/repo", [item])
        warehouse.update_coverage(
            "owner/repo",
            "issues",
            run_id="first",
            refreshed_at=refreshed_at,
            pages_fetched=1,
            max_pages=1,
            truncated=True,
        )
        warehouse.update_coverage(
            "owner/repo",
            "issues",
            run_id="incremental",
            refreshed_at=refreshed_at,
            pages_fetched=1,
            max_pages=10,
            truncated=False,
        )
        row = warehouse.connection.execute(
            """
            SELECT history_complete, last_run_truncated, record_count
            FROM collection_coverage
            WHERE repo_full_name = 'owner/repo' AND entity_type = 'issues'
            """
        ).fetchone()

    assert row == (False, False, 1)

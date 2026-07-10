from datetime import UTC, datetime

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

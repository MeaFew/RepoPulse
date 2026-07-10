"""Edge-case tests for window-scoped and maintenance-efficiency metrics.

These deliberately use tiny hand-built datasets in an in-memory DuckDB so each
metric's boundary behavior (self-reply exclusion, bot exclusion, no-response
NULL, window filtering, backlog aging) is pinned down exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from repopulse.metrics import Analytics, Window
from repopulse.storage import Warehouse

REPO = "owner/repo"


def _build(tmp_path, issues, prs, comments, reviews) -> str:
    db = tmp_path / "edge.duckdb"
    with Warehouse(db) as wh:
        wh.initialize()
        wh.upsert_repository(
            {
                "full_name": REPO,
                "description": None,
                "stargazers_count": 0,
                "forks_count": 0,
                "subscribers_count": 0,
                "open_issues_count": 0,
                "default_branch": "main",
                "language": "Python",
                "license": None,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-06-01T00:00:00Z",
                "pushed_at": "2024-06-01T00:00:00Z",
            },
            datetime.now(UTC),
        )
        wh.upsert_issues(REPO, issues)
        wh.upsert_pull_requests(REPO, prs)
        wh.upsert_issue_comments(REPO, comments)
        wh.upsert_pr_reviews(REPO, reviews)
    return str(db)


# ---- issue_response_kpis ------------------------------------------------


def test_issue_first_response_excludes_author_self_reply(tmp_path) -> None:
    now = datetime.now(UTC)
    issue_created = now - timedelta(days=10)
    issues = [
        {
            "number": 1, "state": "closed", "user": {"login": "alice"},
            "title": "x", "labels": [], "comments": 2, "locked": False,
            "created_at": _iso(issue_created),
            "updated_at": _iso(now - timedelta(days=8)),
            "closed_at": _iso(now - timedelta(days=8)),
        }
    ]
    comments = [
        # Author replies to herself at +1h -- must be ignored.
        {"id": 11, "issue_number": 1, "user": {"login": "alice"},
         "body": "bump", "created_at": _iso(issue_created + timedelta(hours=1))},
        # Real maintainer reply at +5h -- this is the first response.
        {"id": 12, "issue_number": 1, "user": {"login": "bob"},
         "body": "fix incoming", "created_at": _iso(issue_created + timedelta(hours=5))},
    ]
    db = _build(tmp_path, issues, [], comments, [])
    with Analytics(db) as a:
        kpis = a.issue_response_kpis(REPO)

    assert kpis["responded_issues"] == 1
    assert kpis["median_first_response_hours"] == 5.0
    assert kpis["no_response"] == 0


def test_issue_first_response_excludes_bots(tmp_path) -> None:
    now = datetime.now(UTC)
    issue_created = now - timedelta(days=5)
    issues = [
        {
            "number": 2, "state": "open", "user": {"login": "alice"},
            "title": "x", "labels": [], "comments": 1, "locked": False,
            "created_at": _iso(issue_created),
            "updated_at": _iso(now - timedelta(days=4)),
            "closed_at": None,
        }
    ]
    comments = [
        # dependabot + a [bot] -- both ignored.
        {"id": 21, "issue_number": 2, "user": {"login": "dependabot"},
         "body": "bump deps", "created_at": _iso(issue_created + timedelta(hours=1))},
        {"id": 22, "issue_number": 2, "user": {"login": "renovate[bot]"},
         "body": "renovate", "created_at": _iso(issue_created + timedelta(hours=2))},
        # First human reply.
        {"id": 23, "issue_number": 2, "user": {"login": "bob"},
         "body": "looking", "created_at": _iso(issue_created + timedelta(hours=24))},
    ]
    db = _build(tmp_path, issues, [], comments, [])
    with Analytics(db) as a:
        kpis = a.issue_response_kpis(REPO)

    assert kpis["responded_issues"] == 1
    assert kpis["no_response"] == 0


def test_issue_with_no_response_reported_separately(tmp_path) -> None:
    now = datetime.now(UTC)
    issues = [
        {
            "number": 3, "state": "open", "user": {"login": "alice"},
            "title": "x", "labels": [], "comments": 0, "locked": False,
            "created_at": _iso(now - timedelta(days=20)),
            "updated_at": _iso(now - timedelta(days=20)),
            "closed_at": None,
        }
    ]
    db = _build(tmp_path, issues, [], [], [])
    with Analytics(db) as a:
        kpis = a.issue_response_kpis(REPO)

    assert kpis["responded_issues"] == 0
    assert kpis["median_first_response_hours"] is None
    assert kpis["total_issues"] == 1
    assert kpis["no_response"] == 1


# ---- pr_response_kpis ---------------------------------------------------


def test_pr_first_review_excludes_author_and_bot(tmp_path) -> None:
    now = datetime.now(UTC)
    pr_created = now - timedelta(days=6)
    prs = [
        {
            "number": 10, "state": "closed", "user": {"login": "alice"},
            "title": "x", "draft": False, "comments": 0, "review_comments": 0,
            "created_at": _iso(pr_created),
            "updated_at": _iso(now - timedelta(days=5)),
            "closed_at": _iso(now - timedelta(days=5)),
            "merged_at": _iso(now - timedelta(days=5)),
            "merge_commit_sha": "abc",
        }
    ]
    reviews = [
        {"id": 101, "pr_number": 10, "user": {"login": "alice"},
         "state": "commented", "body": "self",
         "submitted_at": _iso(pr_created + timedelta(hours=4))},
        {"id": 102, "pr_number": 10, "user": {"login": "github-actions[bot]"},
         "state": "commented", "body": "ci",
         "submitted_at": _iso(pr_created + timedelta(hours=5))},
        {"id": 103, "pr_number": 10, "user": {"login": "carol"},
         "state": "approved", "body": "lgtm",
         "submitted_at": _iso(pr_created + timedelta(hours=14))},
    ]
    db = _build(tmp_path, [], prs, [], reviews)
    with Analytics(db) as a:
        kpis = a.pr_response_kpis(REPO)

    assert kpis["reviewed_prs"] == 1
    assert kpis["median_first_review_hours"] == 14.0
    assert kpis["no_review"] == 0


# ---- window filtering ---------------------------------------------------


def test_window_filters_issues_by_creation_date(tmp_path) -> None:
    now = datetime.now(UTC)
    issues = [
        {
            "number": i, "state": "closed", "user": {"login": "alice"},
            "title": "x", "labels": [], "comments": 0, "locked": False,
            "created_at": _iso(now - timedelta(days=age)),
            "updated_at": _iso(now - timedelta(days=age)),
            "closed_at": _iso(now - timedelta(days=age - 1)),
        }
        for i, age in [(100, 400), (200, 10), (300, 5)]
    ]
    db = _build(tmp_path, issues, [], [], [])
    window = Window(start=now - timedelta(days=30), end=None)
    with Analytics(db) as a:
        kpis = a.issue_kpis(REPO, window)
        full = a.issue_kpis(REPO)

    assert full["total"] == 3
    assert kpis["total"] == 2  # only the two issues created in the last 30 days


def test_window_filters_monthly_activity(tmp_path) -> None:
    now = datetime.now(UTC)
    issues = [
        {
            "number": 1, "state": "closed", "user": {"login": "alice"},
            "title": "x", "labels": [], "comments": 0, "locked": False,
            "created_at": _iso(now - timedelta(days=400)),
            "updated_at": _iso(now - timedelta(days=400)),
            "closed_at": _iso(now - timedelta(days=399)),
        },
        {
            "number": 2, "state": "open", "user": {"login": "bob"},
            "title": "y", "labels": [], "comments": 0, "locked": False,
            "created_at": _iso(now - timedelta(days=20)),
            "updated_at": _iso(now - timedelta(days=20)),
            "closed_at": None,
        },
    ]
    db = _build(tmp_path, issues, [], [], [])
    window = Window(start=now - timedelta(days=90), end=None)
    with Analytics(db) as a:
        monthly = a.monthly_activity(REPO, window)
        full = a.monthly_activity(REPO)

    assert len(full) >= 2  # spans multiple months
    assert len(monthly) == 1  # only the recent issue


# ---- backlog_kpis -------------------------------------------------------


def test_backlog_90_day_ratio(tmp_path) -> None:
    now = datetime.now(UTC)
    issues = [
        {
            "number": i, "state": "open", "user": {"login": "alice"},
            "title": "x", "labels": [], "comments": 0, "locked": False,
            "created_at": _iso(now - timedelta(days=age)),
            "updated_at": _iso(now - timedelta(days=age)),
            "closed_at": None,
        }
        for i, age in [(1, 200), (2, 100), (3, 10), (4, 5)]  # 2 stale-90, 2 fresh
    ]
    db = _build(tmp_path, issues, [], [], [])
    with Analytics(db) as a:
        kpis = a.backlog_kpis(REPO)

    assert kpis["open_issues"] == 4
    assert kpis["issue_stale_90"] == 2
    assert kpis["issue_backlog_90_pct"] == 50.0


# ---- comparison_kpis ----------------------------------------------------


def test_comparison_kpis_returns_one_row_per_repo(tmp_path) -> None:
    db_path = tmp_path / "cmp.duckdb"
    with Warehouse(db_path) as wh:
        wh.initialize()
        for repo in ["owner/a", "owner/b"]:
            wh.upsert_repository(
                {
                    "full_name": repo, "description": None,
                    "stargazers_count": 10, "forks_count": 1,
                    "subscribers_count": 1, "open_issues_count": 1,
                    "default_branch": "main", "language": "Python", "license": None,
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-06-01T00:00:00Z",
                    "pushed_at": "2024-06-01T00:00:00Z",
                },
                datetime.now(UTC),
            )
            wh.upsert_issues(
                repo,
                [
                    {
                        "number": 1, "state": "closed", "user": {"login": "x"},
                        "title": "y", "labels": [], "comments": 0, "locked": False,
                        "created_at": "2024-05-01T00:00:00Z",
                        "updated_at": "2024-05-02T00:00:00Z",
                        "closed_at": "2024-05-02T00:00:00Z",
                    }
                ],
            )
    with Analytics(str(db_path)) as a:
        df = a.comparison_kpis(["owner/a", "owner/b"])

    assert len(df) == 2
    assert set(df["repository"]) == {"owner/a", "owner/b"}
    assert "issue_close_rate" in df.columns


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")

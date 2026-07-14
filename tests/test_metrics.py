from repopulse.metrics import Analytics
from repopulse.sample_data import DEMO_REPOSITORY, load_demo_data


def test_demo_data_produces_consistent_core_metrics(tmp_path) -> None:
    db_path = tmp_path / "demo.duckdb"
    load_demo_data(db_path)

    with Analytics(db_path) as analytics:
        overview = analytics.overview(DEMO_REPOSITORY)
        issue = analytics.issue_kpis(DEMO_REPOSITORY)
        pr = analytics.pr_kpis(DEMO_REPOSITORY)
        contributor = analytics.contributor_kpis(DEMO_REPOSITORY)
        monthly = analytics.monthly_activity(DEMO_REPOSITORY)
        retention = analytics.contributor_retention(DEMO_REPOSITORY)
        coverage = analytics.data_coverage(DEMO_REPOSITORY)
        quality = analytics.data_quality_flags(DEMO_REPOSITORY)
        tasks = analytics.maintainer_tasks(DEMO_REPOSITORY)

    assert overview["commits"] == 320
    assert overview["releases"] == 12
    assert issue["total"] == 80
    assert issue["open"] == 16
    assert issue["close_rate"] == 80.0
    assert pr["total"] == 60
    assert 50 < pr["merge_rate"] < 80
    assert contributor["contributors"] == 8
    assert not monthly.empty
    assert not retention.empty
    assert retention["retention_rate"].between(0, 100).all()
    assert len(coverage) == 6
    assert coverage["history_complete"].all()
    assert any(flag.level == "good" for flag in quality)
    assert not tasks.empty
    assert {"priority", "task_type", "reason", "url"}.issubset(tasks.columns)


def test_risk_flags_have_explanations(tmp_path) -> None:
    db_path = tmp_path / "demo.duckdb"
    load_demo_data(db_path)

    with Analytics(db_path) as analytics:
        flags = analytics.risk_flags(DEMO_REPOSITORY)

    assert flags
    assert all(flag.title and flag.detail for flag in flags)

from __future__ import annotations

from repopulse.metrics import Analytics, Window
from repopulse.report import build_report
from repopulse.sample_data import DEMO_REPOSITORY, load_demo_data


def test_report_has_findings_risks_recommendations(tmp_path) -> None:
    db_path = tmp_path / "demo.duckdb"
    load_demo_data(db_path)
    with Analytics(db_path) as analytics:
        report = build_report(analytics, DEMO_REPOSITORY)

    md = report.to_markdown()
    html = report.to_html()

    assert "主要发现" in md
    assert "风险" in md
    assert "建议" in md
    assert DEMO_REPOSITORY in md
    assert DEMO_REPOSITORY in html
    assert "<table>" in html
    assert isinstance(report.findings, list) and report.findings
    assert isinstance(report.recommendations, list)
    assert isinstance(report.metrics, dict) and report.metrics


def test_report_respects_window(tmp_path) -> None:
    db_path = tmp_path / "demo.duckdb"
    load_demo_data(db_path)
    from datetime import UTC, datetime, timedelta

    window = Window(start=datetime.now(UTC) - timedelta(days=30), end=None)
    with Analytics(db_path) as analytics:
        report = build_report(analytics, DEMO_REPOSITORY, window)
        full_report = build_report(analytics, DEMO_REPOSITORY)

    # A 30-day window sees fewer issues than the lifetime view.
    assert report.metrics["Issue 总数（窗口）"] <= full_report.metrics["Issue 总数（窗口）"]
    assert "时间范围" in report.to_markdown()

from __future__ import annotations

from datetime import UTC, datetime

from repopulse.metrics import Analytics, RiskFlag, Window
from repopulse.report import Report, build_report
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


def test_html_report_escapes_injected_markup() -> None:
    report = Report(
        repository="owner/<script>alert('xss')</script>",
        generated_at=datetime.now(UTC),
        window=Window.all(),
        findings=["<script>alert('finding')</script>"],
        risks=[RiskFlag("high", "<img src=x onerror=alert(1)>", "detail <b>bold</b>")],
        recommendations=["<svg onload=alert(1)>"],
        metrics={"<td>key</td>": "<script>alert('metric')</script>"},
    )

    rendered = report.to_html()

    assert "<script>" not in rendered
    assert "<img" not in rendered
    assert "<svg" not in rendered
    assert "<td>key</td>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&lt;img" in rendered
    assert "&lt;svg" in rendered

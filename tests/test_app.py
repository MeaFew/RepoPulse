from pathlib import Path

from streamlit.testing.v1 import AppTest

import repopulse.metrics as metrics_module


def test_demo_app_renders_without_exceptions(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("REPOPULSE_DEMO_MODE", "true")
    monkeypatch.setenv("REPOPULSE_DB_PATH", str(tmp_path / "app.duckdb"))

    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=60).run()

    assert not app.exception
    assert [title.value for title in app.title] == ["RepoPulse"]
    assert "单仓库分析" in [tab.label for tab in app.tabs]
    assert "多仓库对比" in [tab.label for tab in app.tabs]


def test_demo_app_recovers_from_stale_metrics_module(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("REPOPULSE_DEMO_MODE", "true")
    monkeypatch.setenv("REPOPULSE_DB_PATH", str(tmp_path / "stale-module.duckdb"))
    monkeypatch.delattr(metrics_module, "Window")

    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=60).run()

    assert not app.exception
    assert hasattr(metrics_module, "Window")

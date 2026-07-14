from pathlib import Path

from streamlit.testing.v1 import AppTest

import repopulse.metrics as metrics_module
from repopulse.sample_data import DEMO_REPOSITORY, load_demo_data


def test_demo_app_renders_without_exceptions(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("REPOPULSE_DEMO_MODE", "true")
    monkeypatch.setenv("REPOPULSE_DB_PATH", str(tmp_path / "app.duckdb"))
    monkeypatch.setenv("REPOPULSE_SNAPSHOT_PATH", str(tmp_path / "missing.duckdb"))

    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=60).run()

    assert not app.exception
    assert [title.value for title in app.title] == ["RepoPulse"]
    assert "单仓库分析" in [tab.label for tab in app.tabs]
    assert "多仓库对比" in [tab.label for tab in app.tabs]


def test_demo_app_recovers_from_stale_metrics_module(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("REPOPULSE_DEMO_MODE", "true")
    monkeypatch.setenv("REPOPULSE_DB_PATH", str(tmp_path / "stale-module.duckdb"))
    monkeypatch.setenv("REPOPULSE_SNAPSHOT_PATH", str(tmp_path / "missing.duckdb"))
    monkeypatch.delattr(metrics_module, "Window")

    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=60).run()

    assert not app.exception
    assert hasattr(metrics_module, "Window")


def test_demo_app_prefers_real_snapshot_over_stale_runtime(monkeypatch, tmp_path) -> None:
    snapshot_path = tmp_path / "snapshot.duckdb"
    runtime_path = tmp_path / "runtime.duckdb"
    load_demo_data(snapshot_path)
    runtime_path.write_bytes(b"stale generated demo")
    monkeypatch.setenv("REPOPULSE_DEMO_MODE", "true")
    monkeypatch.setenv("REPOPULSE_DB_PATH", str(runtime_path))
    monkeypatch.setenv("REPOPULSE_SNAPSHOT_PATH", str(snapshot_path))

    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=60).run()

    assert not app.exception
    assert "当前展示每日更新的真实仓库快照" in [item.value for item in app.success]
    assert app.selectbox[0].value == DEMO_REPOSITORY

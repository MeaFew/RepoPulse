from repopulse.demo_runtime import fallback_database_path, select_demo_database
from repopulse.metrics import Analytics
from repopulse.sample_data import DEMO_REPOSITORY, load_demo_data


def test_real_snapshot_replaces_stale_runtime_database(tmp_path) -> None:
    snapshot = tmp_path / "snapshot.duckdb"
    runtime = tmp_path / "runtime.duckdb"
    load_demo_data(snapshot)
    runtime.write_bytes(b"stale generated demo")

    selection = select_demo_database(runtime, snapshot)

    assert selection.uses_snapshot is True
    assert selection.db_path == runtime
    with Analytics(runtime) as analytics:
        assert analytics.repositories() == [DEMO_REPOSITORY]


def test_missing_snapshot_keeps_configured_database(tmp_path) -> None:
    runtime = tmp_path / "runtime.duckdb"

    selection = select_demo_database(runtime, tmp_path / "missing.duckdb")

    assert selection.uses_snapshot is False
    assert selection.db_path == runtime
    assert fallback_database_path(runtime) == tmp_path / "runtime-fallback.duckdb"

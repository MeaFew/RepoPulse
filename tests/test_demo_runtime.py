from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import repopulse.demo_runtime as demo_runtime
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


def test_concurrent_snapshot_copies_use_isolated_temporary_files(tmp_path, monkeypatch) -> None:
    snapshot = tmp_path / "snapshot.duckdb"
    runtime = tmp_path / "runtime.duckdb"
    load_demo_data(snapshot)
    barrier = Barrier(2)
    real_copy2 = demo_runtime.copy2

    def synchronized_copy(source, target):
        copied = real_copy2(source, target)
        barrier.wait()
        return copied

    monkeypatch.setattr(demo_runtime, "copy2", synchronized_copy)

    with ThreadPoolExecutor(max_workers=2) as executor:
        selections = list(executor.map(lambda _: select_demo_database(runtime, snapshot), range(2)))

    assert all(selection.uses_snapshot for selection in selections)
    assert not list(tmp_path.glob("*.copying"))
    with Analytics(runtime) as analytics:
        assert analytics.repositories() == [DEMO_REPOSITORY]

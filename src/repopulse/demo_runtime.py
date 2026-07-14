from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from shutil import copy2
from tempfile import NamedTemporaryFile


@dataclass(frozen=True)
class DemoDatabaseSelection:
    db_path: Path
    uses_snapshot: bool


def select_demo_database(
    configured_db_path: Path,
    snapshot_path: Path,
) -> DemoDatabaseSelection:
    """Prefer a committed real-data snapshot and mirror it to writable storage.

    Streamlit Community Cloud may keep ``/tmp`` across app sleeps. Without an
    explicit mirror, an old generated demo database can permanently shadow a
    real snapshot added by a later deployment. The sidecar signature avoids
    copying the multi-megabyte snapshot on every Streamlit rerun while still
    replacing stale runtime data after a new snapshot is deployed.
    """
    if not snapshot_path.is_file():
        return DemoDatabaseSelection(configured_db_path, False)

    source = snapshot_path.resolve()
    target = configured_db_path.resolve()
    if source == target:
        return DemoDatabaseSelection(snapshot_path, True)

    target.parent.mkdir(parents=True, exist_ok=True)
    source_stat = source.stat()
    signature = f"{source_stat.st_size}:{source_stat.st_mtime_ns}"
    marker = target.with_suffix(f"{target.suffix}.snapshot-source")
    current_signature = marker.read_text(encoding="utf-8") if marker.is_file() else None

    if not target.is_file() or current_signature != signature:
        # Streamlit may run more than one session while a deployment wakes up.
        # Give every copier its own file so concurrent reruns cannot replace a
        # shared ``.copying`` path while another copy is still in progress.
        with NamedTemporaryFile(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".copying",
            delete=False,
        ) as temporary_file:
            temporary = Path(temporary_file.name)

        try:
            copy2(source, temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        marker.write_text(signature, encoding="utf-8")

    return DemoDatabaseSelection(configured_db_path, True)


def fallback_database_path(configured_db_path: Path) -> Path:
    """Return an isolated path for generated data when a snapshot is unusable."""
    return configured_db_path.with_name(
        f"{configured_db_path.stem}-fallback{configured_db_path.suffix}"
    )

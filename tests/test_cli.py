from __future__ import annotations

import json

from repopulse.cli import main
from repopulse.sample_data import DEMO_REPOSITORY


def test_cli_demo_and_summary(tmp_path, capsys) -> None:
    db_path = tmp_path / "cli.duckdb"

    exit_code = main(["--db", str(db_path), "demo"])
    assert exit_code == 0
    assert DEMO_REPOSITORY in capsys.readouterr().out

    exit_code = main(["--db", str(db_path), "summary", DEMO_REPOSITORY])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["overview"]["repo_full_name"] == DEMO_REPOSITORY
    assert payload["issues"]["total"] == 80
    assert "data_quality" in payload

from __future__ import annotations

import json
from types import SimpleNamespace

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


def _fake_collect_result(repository: str) -> SimpleNamespace:
    return SimpleNamespace(
        repository=repository, counts={}, total_loaded=0, truncated_entities=[]
    )


def test_cli_collect_token_flag_warns(monkeypatch, capsys, tmp_path) -> None:
    captured = {}

    def fake_collect(repository, db_path, *, token, max_pages):
        captured["token"] = token
        return _fake_collect_result(repository)

    monkeypatch.setattr("repopulse.cli.collect_repository", fake_collect)

    exit_code = main(["--db", str(tmp_path / "c.duckdb"), "collect", "duckdb/duckdb",
                      "--token", "secret"])

    assert exit_code == 0
    assert captured["token"] == "secret"
    assert "GITHUB_TOKEN" in capsys.readouterr().err


def test_cli_collect_token_defaults_to_env(monkeypatch, capsys, tmp_path) -> None:
    captured = {}

    def fake_collect(repository, db_path, *, token, max_pages):
        captured["token"] = token
        return _fake_collect_result(repository)

    monkeypatch.setattr("repopulse.cli.collect_repository", fake_collect)
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")

    exit_code = main(["--db", str(tmp_path / "c.duckdb"), "collect", "duckdb/duckdb"])

    assert exit_code == 0
    assert captured["token"] == "env-token"
    assert capsys.readouterr().err == ""

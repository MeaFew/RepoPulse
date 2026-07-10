import pytest

from repopulse.config import Settings, validate_repository


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("duckdb/duckdb", "duckdb/duckdb"), (" owner/repo/ ", "owner/repo")],
)
def test_validate_repository(raw: str, expected: str) -> None:
    assert validate_repository(raw) == expected


@pytest.mark.parametrize("raw", ["repo", "a/b/c", "/repo", "owner/", "../repo"])
def test_validate_repository_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(ValueError):
        validate_repository(raw)


def test_settings_reads_cloud_demo_mode(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "cloud-demo.duckdb"
    monkeypatch.setenv("REPOPULSE_DB_PATH", str(db_path))
    monkeypatch.setenv("REPOPULSE_DEMO_MODE", "true")
    monkeypatch.setenv("REPOPULSE_MAX_PAGES", "3")

    settings = Settings.from_env()

    assert settings.db_path == db_path
    assert settings.demo_mode is True
    assert settings.max_pages == 3

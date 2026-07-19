from datetime import UTC, datetime, timedelta, timezone

from repopulse._timeutils import coerce_utc, ensure_utc, parse_iso, to_iso_z


def test_ensure_utc_attaches_tz_to_naive() -> None:
    naive = datetime(2026, 1, 1, 12, 0, 0)
    result = ensure_utc(naive)
    assert result.tzinfo == UTC
    assert result.hour == 12


def test_ensure_utc_converts_aware_to_utc() -> None:
    cst = timezone(offset=timedelta(hours=8))
    aware = datetime(2026, 1, 1, 20, 0, 0, tzinfo=cst)
    result = ensure_utc(aware)
    assert result.tzinfo == UTC
    assert result.hour == 12


def test_to_iso_z_roundtrips() -> None:
    dt = datetime(2026, 6, 15, 8, 30, 0, tzinfo=UTC)
    iso = to_iso_z(dt)
    assert iso == "2026-06-15T08:30:00Z"
    assert parse_iso(iso) == dt


def test_coerce_utc_accepts_string_and_datetime() -> None:
    dt = datetime(2026, 3, 10, 6, 0, 0, tzinfo=UTC)
    assert coerce_utc("2026-03-10T06:00:00Z") == dt
    assert coerce_utc(dt) == dt

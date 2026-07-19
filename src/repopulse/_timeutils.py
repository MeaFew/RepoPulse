"""Shared datetime helpers used across the pipeline, client and sample data."""

from __future__ import annotations

from datetime import UTC, datetime


def ensure_utc(value: datetime) -> datetime:
    """Attach UTC tzinfo to naive datetimes; convert aware ones to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_iso_z(value: datetime) -> str:
    """Serialize a datetime to ISO 8601 with a trailing ``Z`` (UTC)."""
    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    """Parse an ISO 8601 string (with or without ``Z``) into an aware UTC datetime."""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return ensure_utc(dt)


def coerce_utc(value: str | datetime) -> datetime:
    """Accept either a datetime or an ISO string and return an aware UTC datetime."""
    if isinstance(value, datetime):
        return ensure_utc(value)
    return parse_iso(value)

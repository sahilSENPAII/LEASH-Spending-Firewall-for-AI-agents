"""UTC helpers shared by mandates and the audit log.

Times are stored in UTC: SQLite keeps a non-UTC time as its local wall time and drops the offset,
and returns DateTime(timezone=True) columns without tzinfo.
"""

from datetime import UTC, datetime


def require_utc(value: datetime, name: str = "now") -> datetime:
    """`value` converted to UTC. Raises ValueError for a naive or non-datetime value."""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """A time read from the database, with UTC attached if the driver dropped it."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)

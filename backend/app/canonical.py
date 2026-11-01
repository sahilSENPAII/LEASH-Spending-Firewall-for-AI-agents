"""Canonical JSON: one byte string per value, so signatures and audit hashes are stable.

Rules (docs/CONTRACTS.md): keys sorted, no whitespace, UTF-8. Decimal becomes a string,
datetime becomes UTC ISO-8601 with Z. Floats, sets and naive datetimes are rejected because
they have no single exact encoding.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal


def canonical_json(value: object) -> bytes:
    """Encode `value` as canonical JSON bytes. Raises TypeError or ValueError on bad input."""
    return json.dumps(
        _normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _normalize(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        raise TypeError("floats are not allowed in canonical JSON; use Decimal or str")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite Decimal is not allowed in canonical JSON")
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive datetime is not allowed in canonical JSON")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    raise TypeError(f"{type(value).__name__} is not allowed in canonical JSON")

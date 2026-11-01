from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.canonical import canonical_json


def test_keys_sorted_and_compact():
    assert canonical_json({"b": 1, "a": [True, None, "x"]}) == b'{"a":[true,null,"x"],"b":1}'


def test_same_value_same_bytes_regardless_of_key_order():
    assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})


def test_decimal_becomes_string_as_written():
    assert canonical_json({"amount": Decimal("179.00")}) == b'{"amount":"179.00"}'


def test_datetime_becomes_utc_with_z():
    ist = timezone(timedelta(hours=5, minutes=30))
    value = datetime(2026, 11, 2, 17, 30, tzinfo=ist)
    assert canonical_json(value) == b'"2026-11-02T12:00:00Z"'
    assert canonical_json(value) == canonical_json(value.astimezone(UTC))


def test_non_ascii_is_utf8_not_escaped():
    assert canonical_json("café") == '"café"'.encode()


def test_tuple_encodes_as_list():
    assert canonical_json((1, 2)) == b"[1,2]"


@pytest.mark.parametrize("value", [1.5, {"amount": 0.1}, [2.0]])
def test_floats_are_rejected(value):
    with pytest.raises(TypeError):
        canonical_json(value)


@pytest.mark.parametrize("value", [{"x"}, frozenset({"x"}), b"raw", object()])
def test_unsupported_types_are_rejected(value):
    with pytest.raises(TypeError):
        canonical_json(value)


def test_non_string_keys_are_rejected():
    with pytest.raises(TypeError):
        canonical_json({1: "a"})


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError):
        canonical_json(datetime(2026, 11, 2, 12, 0))


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_non_finite_decimal_is_rejected(value):
    with pytest.raises(ValueError):
        canonical_json(value)

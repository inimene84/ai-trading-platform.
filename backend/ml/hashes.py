"""Canonical JSON SHA-256 hashes for train–serve promotion parity."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def canonical_json(obj: Any) -> str:
    """UTF-8 canonical JSON: sorted keys, compact separators, ensure_ascii."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def object_hash(obj: Any) -> str:
    return sha256_hex(canonical_json(obj))


def geometry_hash(geometry: Mapping[str, Any]) -> str:
    """SHA-256 of entire geometry.json minus the `hashes` block."""
    payload = {key: value for key, value in geometry.items() if key != "hashes"}
    return object_hash(payload)


def feature_schema_hash(schema: Mapping[str, Any] | list[Any]) -> str:
    """SHA-256 of the entire feature_schema.json object."""
    return object_hash(schema)


def holdout_id_hash(*, start: str, end: str, instrument: str, timeframe: str) -> str:
    """SHA-256 of the sealed holdout identity tuple."""
    return object_hash(
        {
            "start": start,
            "end": end,
            "instrument": instrument,
            "timeframe": timeframe,
        }
    )


def is_sha256_hex(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True

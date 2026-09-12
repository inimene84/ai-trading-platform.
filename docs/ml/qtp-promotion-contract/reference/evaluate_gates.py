#!/usr/bin/env python3
"""Reference evaluator for QTP Promotion Contract v1.0.0.

Exit 0 PROMOTE/SHADOW, 2 REJECT (or ROLLBACK), 3 usage/schema error.

    python reference/evaluate_gates.py \
      --geometry examples/geometry.json \
      --metrics examples/metrics.pass.json \
      --live-geometry examples/geometry.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

CONTRACT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CONTRACT_ROOT.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.ml.promotion_gates import evaluate_promotion_gates  # noqa: E402


REQUIRED_GEOMETRY = ("spec_version", "triple_barrier", "bar", "costs", "gates")
REQUIRED_METRICS = (
    "spec_version",
    "hashes",
    "n_trials_source",
    "dsr",
    "dsr_is_probability",
    "pbo",
    "promote_requested",
)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"file not found: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {path}: {exc}") from exc


def _require_object(payload: Any, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SystemExit(f"{label} must be a JSON object")
    return payload


def _check_required(payload: Mapping[str, Any], keys: tuple[str, ...], label: str) -> None:
    missing = [key for key in keys if key not in payload]
    if missing:
        raise SystemExit(f"{label} missing required fields: {', '.join(missing)}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QTP Promotion Contract v1.0.0 gate evaluator")
    parser.add_argument("--geometry", required=True, type=Path, help="Path to geometry.json")
    parser.add_argument("--metrics", required=True, type=Path, help="Path to metrics.json")
    parser.add_argument("--live-geometry", type=Path, default=None, help="VPS/live geometry.json")
    parser.add_argument("--feature-schema", type=Path, default=None, help="feature_schema.json")
    parser.add_argument("--holdout-already-spent", action="store_true")
    parser.add_argument("--production-kill", action="store_true")
    parser.add_argument("--production-drift", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        if exc.code not in (0, None):
            return 3
        raise

    try:
        geometry = _require_object(_load_json(args.geometry), "geometry")
        metrics = _require_object(_load_json(args.metrics), "metrics")
        _check_required(geometry, REQUIRED_GEOMETRY, "geometry")
        _check_required(metrics, REQUIRED_METRICS, "metrics")
        live_geometry = geometry
        if args.live_geometry is not None:
            live_geometry = _require_object(_load_json(args.live_geometry), "live-geometry")
            _check_required(live_geometry, REQUIRED_GEOMETRY, "live-geometry")
        feature_schema = None
        if args.feature_schema is not None:
            loaded = _load_json(args.feature_schema)
            if not isinstance(loaded, (dict, list)):
                raise SystemExit("feature-schema must be a JSON object or array")
            feature_schema = loaded
    except SystemExit as exc:
        message = str(exc)
        if message:
            print(message, file=sys.stderr)
        return 3

    result = evaluate_promotion_gates(
        geometry,
        metrics,
        live_geometry=live_geometry,
        feature_schema=feature_schema,
        holdout_already_spent=args.holdout_already_spent,
        production_kill=args.production_kill,
        production_drift=args.production_drift,
    )
    print(result.verdict)
    print(result.reason)
    if result.failed_gate:
        print(f"failed_gate={result.failed_gate}")
    for warning in result.warnings:
        print(f"warn: {warning}")
    if result.verdict in {"PROMOTE", "SHADOW"}:
        return 0
    if result.verdict in {"REJECT", "ROLLBACK"}:
        return 2
    return 3


if __name__ == "__main__":
    sys.exit(main())

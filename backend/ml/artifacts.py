"""Read/write promotion artifacts. GPU jobs write; the engine only re-reads."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from backend.ml.hashes import feature_schema_hash, geometry_hash, is_sha256_hex
from backend.ml.holdout_registry import HoldoutRegistry, default_registry_path

ARTIFACT_DIR_ENV = "QTP_PROMOTION_ARTIFACT_DIR"
GEOMETRY_NAME = "geometry.json"
METRICS_NAME = "metrics.json"
FEATURE_SCHEMA_NAME = "feature_schema.json"
TRIALS_RETURNS_NAME = "trials_returns.parquet"
CPCV_PATHS_NAME = "cpcv_paths.parquet"
MODEL_NAME = "model.joblib"
ENV_LOCK_NAME = "env.lock"


def default_artifact_dir() -> Optional[Path]:
    override = os.getenv(ARTIFACT_DIR_ENV, "").strip()
    if override:
        return Path(override)
    return None


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return raw


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


@dataclass
class PromotionBundle:
    directory: Path
    geometry: dict[str, Any]
    metrics: dict[str, Any]
    feature_schema: dict[str, Any] | None = None

    @property
    def geometry_hash(self) -> str:
        return str(self.metrics.get("hashes", {}).get("geometry_hash") or geometry_hash(self.geometry))

    @property
    def feature_schema_hash(self) -> Optional[str]:
        hashed = self.metrics.get("hashes", {}).get("feature_schema_hash")
        return str(hashed) if hashed else None


def load_promotion_bundle(directory: Path | None = None) -> Optional[PromotionBundle]:
    root = directory or default_artifact_dir()
    if root is None or not Path(root).exists():
        return None
    root = Path(root)
    geometry_path = root / GEOMETRY_NAME
    metrics_path = root / METRICS_NAME
    if not geometry_path.exists() or not metrics_path.exists():
        return None
    feature_schema = None
    feature_path = root / FEATURE_SCHEMA_NAME
    if feature_path.exists():
        feature_schema = _read_json(feature_path)
    return PromotionBundle(
        directory=root,
        geometry=_read_json(geometry_path),
        metrics=_read_json(metrics_path),
        feature_schema=feature_schema,
    )


def attach_geometry_hashes(
    geometry: dict[str, Any],
    feature_schema: Mapping[str, Any] | None = None,
    *,
    holdout_id: str | None = None,
) -> dict[str, Any]:
    """Fill geometry.hashes after the rest of the object is frozen."""
    body = {key: value for key, value in geometry.items() if key != "hashes"}
    hashes: dict[str, Any] = {"geometry_hash": geometry_hash(body)}
    if feature_schema is not None:
        hashes["feature_schema_hash"] = feature_schema_hash(feature_schema)
    if holdout_id:
        hashes["holdout_id"] = holdout_id
    out = dict(body)
    out["hashes"] = hashes
    return out


def validate_bundle_hashes(bundle: PromotionBundle) -> list[str]:
    """Return human-readable hash problems (empty list = ok)."""
    problems: list[str] = []
    hashes = bundle.metrics.get("hashes") or {}
    expected_g = geometry_hash(bundle.geometry)
    got_g = hashes.get("geometry_hash")
    if not is_sha256_hex(got_g) or got_g != expected_g:
        problems.append("geometry_hash mismatch")
    if bundle.feature_schema is not None:
        expected_f = feature_schema_hash(bundle.feature_schema)
        got_f = hashes.get("feature_schema_hash")
        if not is_sha256_hex(got_f) or got_f != expected_f:
            problems.append("feature_schema_hash mismatch")
    return problems


def holdout_registry_for_bundle(bundle: PromotionBundle) -> HoldoutRegistry:
    registry_path = default_registry_path()
    if registry_path.parent == Path("backend/ml/artifacts"):
        registry_path = bundle.directory / "holdout_registry.json"
    return HoldoutRegistry(registry_path)

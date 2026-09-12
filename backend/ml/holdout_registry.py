"""Sealed holdout registry: a holdout_id may be peeked once, then spent forever."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_REGISTRY_ENV = "QTP_HOLDOUT_REGISTRY_PATH"
DEFAULT_REGISTRY_NAME = "holdout_registry.json"


def default_registry_path() -> Path:
    override = os.getenv(DEFAULT_REGISTRY_ENV, "").strip()
    if override:
        return Path(override)
    artifact_dir = os.getenv("QTP_PROMOTION_ARTIFACT_DIR", "").strip()
    if artifact_dir:
        return Path(artifact_dir) / DEFAULT_REGISTRY_NAME
    return Path("backend/ml/artifacts") / DEFAULT_REGISTRY_NAME


class HoldoutRegistry:
    def __init__(self, path: Path | None = None):
        self.path = path or default_registry_path()
        self._data: dict[str, Any] = {"spent": {}}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = raw
                self._data.setdefault("spent", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def record(self, holdout_id: str) -> dict[str, Any]:
        spent = self._data.get("spent", {})
        raw = spent.get(str(holdout_id)) or {}
        return dict(raw) if isinstance(raw, dict) else {"spent": True}

    def is_spent(self, holdout_id: str) -> bool:
        return str(holdout_id) in self._data.get("spent", {})

    def is_second_peek(self, holdout_id: str, *, geometry_hash: str, feature_schema_hash: str) -> bool:
        """True when this holdout_id was already consumed by a different artifact."""
        if not self.is_spent(holdout_id):
            return False
        recorded = self.record(holdout_id)
        same_artifact = (
            recorded.get("geometry_hash") == geometry_hash
            and recorded.get("feature_schema_hash") == feature_schema_hash
        )
        return not same_artifact

    def mark_spent(self, holdout_id: str, *, meta: dict[str, Any] | None = None) -> None:
        spent = self._data.setdefault("spent", {})
        spent[str(holdout_id)] = meta or {"spent": True}
        self.save()

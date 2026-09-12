"""QTP promotion contract — train/serve parity for Jesse ML artifacts.

The GPU training job writes geometry/metrics; the Decision Engine re-reads
those files, verifies hashes, and walks the boolean gates. This package does
not place orders.
"""

from backend.ml.geometry import (
    HOUSE_ATR_PERIOD,
    HOUSE_BAR_PRIMARY_TYPE,
    HOUSE_BAR_TIMEFRAME,
    HOUSE_PT_ATR_MULT,
    HOUSE_SL_ATR_MULT,
    HOUSE_VERTICAL_TIMEOUT_BARS,
    SPEC_VERSION,
    live_locked_fields,
    training_barrier_multipliers,
)
from backend.ml.hashes import (
    canonical_json,
    feature_schema_hash,
    geometry_hash,
    holdout_id_hash,
    sha256_hex,
)
from backend.ml.promotion_gates import (
    GateResult,
    Verdict,
    evaluate_promotion_gates,
)

__all__ = [
    "HOUSE_ATR_PERIOD",
    "HOUSE_BAR_PRIMARY_TYPE",
    "HOUSE_BAR_TIMEFRAME",
    "HOUSE_PT_ATR_MULT",
    "HOUSE_SL_ATR_MULT",
    "HOUSE_VERTICAL_TIMEOUT_BARS",
    "SPEC_VERSION",
    "GateResult",
    "Verdict",
    "canonical_json",
    "evaluate_promotion_gates",
    "feature_schema_hash",
    "geometry_hash",
    "holdout_id_hash",
    "live_locked_fields",
    "sha256_hex",
    "training_barrier_multipliers",
]

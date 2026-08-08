"""Confidence constants and the ambiguous-resolution confidence formula."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Confidence constants (legacy string-based; still used for back-compat)
# ---------------------------------------------------------------------------

CONF_HIGH = "high"
CONF_LOW = "low"
CONF_NONE = "none"

# Maximum MRO depth to prevent infinite loops in pathological inheritance
_MAX_MRO_DEPTH = 20


def _ambiguous_confidence(num_candidates: int) -> float:
    """Compute confidence score for ambiguous resolution based on candidate count.

    When a call site has multiple possible targets (interface implementors,
    multiple constructor-assigned types), confidence decays with the number
    of candidates — lower certainty when choosing among many options.

    Formula: max(0.15, 0.6 / max(1, num_candidates))
    - 1 candidate: 0.6 (still "low" confidence per >=0.7 derivation, but highest ambiguous score)
    - 2 candidates: 0.3
    - 3 candidates: 0.2
    - 5+ candidates: 0.15 (floor)

    This ensures all ambiguous edges remain "low" under the legacy >=0.7 "high" boundary,
    while still preserving the numeric gradient for ranking/filtering downstream (3c).

    Args:
        num_candidates: Number of possible resolution targets (>=1).

    Returns:
        Confidence score in [0.15, 0.6].
    """
    return max(0.15, 0.6 / max(1, num_candidates))

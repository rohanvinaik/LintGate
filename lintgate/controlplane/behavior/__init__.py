"""Behavior subpackage — behavioral compass types, hypothesis management, and predictions.

Re-exports all public names so that imports from the old flat module paths
(e.g. ``from lintgate.controlplane.behavior_compass import ...``) continue
to work via backward-compat shims in the parent package.

Canonical imports should use this subpackage directly:
    from lintgate.controlplane.behavior.types import BehaviorCompass
    from lintgate.controlplane.behavior.compass import record_tool_event
"""

from __future__ import annotations

# ── Core compass (record_tool_event and helpers) ─────────────────────────
from .compass import (  # noqa: F401
    _auto_generate_hypothesis,
    _parse_bash_event,
    _update_approach,
    _update_error_memory,
    _update_integration_counters,
    record_tool_event,
)

# ── Hypothesis management ────────────────────────────────────────────────
from .compass_hypothesis import (  # noqa: F401
    _find_conflicting_hypotheses,
    _find_low_confidence_hypotheses,
    _find_uncovered_approaches,
    _hypothesis_matches_sig,
    _strengthen_hypothesis,
    _test_hypotheses,
    _weaken_hypothesis,
    add_declared_hypothesis,
    compute_coverage,
    compute_uncertainty_zones,
    decay_stale,
    evict_overflow,
    find_relevant_hypotheses,
    update_hypothesis,
)

# ── Prediction tracking ─────────────────────────────────────────────────
from .compass_predictions import (  # noqa: F401
    _apply_prediction_to_hypothesis,
    _check_predictions,
    _evaluate_prediction_match,
    compute_prediction_accuracy,
)

# ── Types and constants ──────────────────────────────────────────────────
from .types import (  # noqa: F401
    DEFAULT_HYPOTHESIS_CONFIG,
    DEFAULT_THRESHOLDS,
    MAX_ACTION_HISTORY,
    MAX_APPROACHES,
    MAX_ERROR_MEMORY,
    MAX_EVIDENCE_ITEMS,
    ApproachAttempt,
    BehaviorCompass,
    BehaviorHypothesis,
    CoverageMetrics,
    NudgeState,
    Prediction,
    PredictionExpectation,
    PredictionStateContainer,
    SignalState,
    make_hypothesis_id,
    new_compass,
)

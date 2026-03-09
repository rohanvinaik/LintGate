"""Backward-compatibility shim — canonical location is now behavior/compass.py.

All public names are re-exported so existing imports continue to work:
    from lintgate.controlplane.behavior_compass import record_tool_event
    from lintgate.controlplane.behavior_compass import BehaviorCompass
"""

# Re-export everything from the new canonical locations via the subpackage
from lintgate.controlplane.behavior.compass import (  # noqa: F401
    _auto_generate_hypothesis,
    _parse_bash_event,
    _update_approach,
    _update_error_memory,
    _update_integration_counters,
    record_tool_event,
)
from lintgate.controlplane.behavior.compass_hypothesis import (  # noqa: F401
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
from lintgate.controlplane.behavior.compass_predictions import (  # noqa: F401
    _apply_prediction_to_hypothesis,
    _check_predictions,
    _evaluate_prediction_match,
    compute_prediction_accuracy,
)
from lintgate.controlplane.behavior.types import (  # noqa: F401
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
from lintgate.controlplane.command_normalization import (  # noqa: F401
    DEFAULT_INTENT_MAP,
    DEFAULT_INTENT_SIG_MAP,
    INTENT_CATEGORIES,
    error_memory_key,
    extract_error_sig,
    normalize_command_sig,
    resolve_intent,
)

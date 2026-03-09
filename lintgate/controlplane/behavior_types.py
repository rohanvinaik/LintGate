"""Backward-compatibility shim — canonical location is now behavior/types.py.

All public names are re-exported so existing imports continue to work:
    from lintgate.controlplane.behavior_types import BehaviorCompass
"""

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

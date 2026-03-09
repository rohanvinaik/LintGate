"""Backward-compatibility shim — canonical location is now behavior/compass_predictions.py.

All public names are re-exported so existing imports continue to work:
    from lintgate.controlplane.behavior_compass_predictions import compute_prediction_accuracy
"""

from lintgate.controlplane.behavior.compass_predictions import (  # noqa: F401
    _apply_prediction_to_hypothesis,
    _check_predictions,
    _evaluate_prediction_match,
    compute_prediction_accuracy,
)

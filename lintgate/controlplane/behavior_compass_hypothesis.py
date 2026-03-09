"""Backward-compatibility shim — canonical location is now behavior/compass_hypothesis.py.

All public names are re-exported so existing imports continue to work:
    from lintgate.controlplane.behavior_compass_hypothesis import update_hypothesis
"""

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

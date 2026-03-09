"""Backward-compatibility shim — canonical location: behavior/channel.py."""

from __future__ import annotations

from .behavior.channel import (  # noqa: F401
    _THEORY_CODA_MAX_CHARS,
    SIGNAL_THEORY_MAP,
    BehaviorChannel,
    _apply_prediction_modulation,
    _build_channel_result,
    _compute_nudge_outcomes,
    _ground_finding_in_theory,
    _load_execute_config,
)
from .behavior.scoring import (  # noqa: F401
    IntentBiasScorer,
    SignalCoordinator,
)

# Aliased private names used by tests
_IntentBiasScorer = IntentBiasScorer  # noqa: F401
_SignalCoordinator = SignalCoordinator  # noqa: F401

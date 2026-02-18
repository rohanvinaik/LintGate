"""Global Behavior Profile — cross-session learning layer.

Aggregates behavioral patterns across sessions over days/weeks, providing
warm-start priors for the intent bias scorer. Stores only aggregate counts —
no raw commands, no output text, no PII.

Design principles:
- Bias, don't gate: global priors adjust bias weights via bounded deltas,
  never produce constraints or findings.
- Decay over session: alpha starts high and decays toward 0 as local
  event_counter grows, so global influence fades as local data accumulates.
- Minimum sample size: priors only activate after MIN_SAMPLE_SIZE sessions.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ── Constants ────────────────────────────────────────────────────────

GLOBAL_PROFILE_PATH = Path.home() / ".claude" / "lintgate" / "global_behavior_profile.json"
DEFAULT_ALPHA = 0.6  # Initial global influence weight
DEFAULT_DECAY_HORIZON = 50  # event_counter at which alpha reaches ~0
MIN_SAMPLE_SIZE = 3  # Minimum session count before global priors activate
PROFILE_TTL_DAYS = 90  # Entries older than this are pruned

# Global bias adjustment cap per signal (inside the ±0.25 bias cap)
_GLOBAL_ADJ_CAP = 0.10
MAX_TRACKED_SESSIONS = 4096


# ── Data Model ───────────────────────────────────────────────────────


@dataclass
class GlobalBehaviorProfile:
    """Cross-session behavioral priors. Advisory only — never gates or produces findings."""

    # Metadata
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    session_count: int = 0  # Total sessions contributing
    last_session_id: str = ""  # Tracks distinct sessions for dedup
    seen_session_ids: list[str] = field(default_factory=list)  # Distinct session IDs seen historically

    # Signal frequency priors: signal_name → {total_firings, sessions_present}
    signal_priors: dict[str, dict[str, int]] = field(default_factory=dict)

    # Intent ratio priors: intent → cumulative count across sessions
    intent_ratios: dict[str, int] = field(default_factory=dict)

    # Nudge outcome tracking: signal_name → {accepted: N, ignored: N}
    nudge_outcomes: dict[str, dict[str, int]] = field(default_factory=dict)

    # Computed bias adjustments (recomputed on each update)
    computed_bias_adjustments: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GlobalBehaviorProfile:
        return cls(
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            session_count=data.get("session_count", 0),
            last_session_id=data.get("last_session_id", ""),
            seen_session_ids=data.get("seen_session_ids", []),
            signal_priors=data.get("signal_priors", {}),
            intent_ratios=data.get("intent_ratios", {}),
            nudge_outcomes=data.get("nudge_outcomes", {}),
            computed_bias_adjustments=data.get("computed_bias_adjustments", {}),
        )


# ── Alpha Decay ──────────────────────────────────────────────────────


def compute_alpha(
    event_counter: int,
    alpha_initial: float = DEFAULT_ALPHA,
    decay_horizon: int = DEFAULT_DECAY_HORIZON,
) -> float:
    """Compute decaying alpha for global prior influence.

    alpha = alpha_initial * max(0, 1 - event_counter / decay_horizon)

    At event 0:  alpha = 0.6 (full global influence)
    At event 25: alpha = 0.3 (half influence)
    At event 50: alpha = 0.0 (fully local)
    """
    if decay_horizon <= 0:
        return 0.0
    return alpha_initial * max(0.0, 1.0 - event_counter / decay_horizon)


# ── Bias Adjustment Computation ──────────────────────────────────────


def compute_bias_adjustments(profile: GlobalBehaviorProfile) -> dict[str, float]:
    """Compute global bias adjustments from aggregate signal data.

    Returns signal_name → delta to add to default bias weight.
    Positive = increase sensitivity (signal fires often for this user).
    Negative = decrease sensitivity (signal fires but user ignores nudge).
    Clamped to [-_GLOBAL_ADJ_CAP, +_GLOBAL_ADJ_CAP] per signal.
    """
    adjustments: dict[str, float] = {}
    if profile.session_count < MIN_SAMPLE_SIZE:
        return adjustments

    for signal, data in profile.signal_priors.items():
        total = data.get("total_firings", 0)
        sessions = data.get("sessions_present", 0)
        if sessions == 0:
            continue

        # Frequency: average firings per session
        freq = total / profile.session_count

        # Nudge effectiveness
        outcomes = profile.nudge_outcomes.get(signal, {})
        accepted = outcomes.get("accepted", 0)
        ignored = outcomes.get("ignored", 0)
        total_nudges = accepted + ignored

        delta = 0.0

        # High frequency → increase sensitivity (agent has this habit)
        if freq >= 2.0:
            delta += 0.05
        elif freq >= 1.0:
            delta += 0.02

        # Nudge acceptance rate modulates
        if total_nudges >= 3:
            accept_rate = accepted / total_nudges
            if accept_rate >= 0.6:
                delta += 0.03  # Nudges are useful, boost
            elif accept_rate <= 0.2:
                delta -= 0.05  # Nudges are noise, dampen

        adjustments[signal] = max(-_GLOBAL_ADJ_CAP, min(_GLOBAL_ADJ_CAP, delta))

    return adjustments


# ── Session Delta Application ────────────────────────────────────────


def apply_session_delta(
    profile: GlobalBehaviorProfile,
    delta: dict[str, Any],
    session_id: str = "",
) -> None:
    """Apply a session's behavioral outcomes to the global profile.

    Called per mesh run (potentially multiple times per session). Uses
    session_id to distinguish new sessions from repeated calls within
    the same session.

    delta keys:
      signal_fire_counts: dict[str, int]  — per-run signal firings
      intent_summary: dict[str, int]      — per-run intent counts
      nudge_outcomes: dict[str, str]      — signal → "accepted" | "ignored"
    """
    profile.updated_at = time.time()

    # Detect new session robustly even with interleaved session ordering.
    seen = set(profile.seen_session_ids)
    is_new_session = bool(session_id and session_id not in seen)
    if is_new_session:
        profile.session_count += 1
        profile.last_session_id = session_id
        profile.seen_session_ids.append(session_id)
        if len(profile.seen_session_ids) > MAX_TRACKED_SESSIONS:
            profile.seen_session_ids = profile.seen_session_ids[-MAX_TRACKED_SESSIONS:]
    elif session_id:
        profile.last_session_id = session_id

    # Merge signal fire counts (additive per-run deltas).
    signal_counts = delta.get("signal_fire_counts", {})
    for signal, count in signal_counts.items():
        count = int(count)
        if count <= 0:
            continue
        if signal not in profile.signal_priors:
            profile.signal_priors[signal] = {"total_firings": 0, "sessions_present": 0}
        profile.signal_priors[signal]["total_firings"] += count
        # sessions_present only incremented once per new session
        if is_new_session:
            profile.signal_priors[signal]["sessions_present"] += 1

    # Merge per-run intent counts
    for intent, count in delta.get("intent_summary", {}).items():
        count = int(count)
        if count <= 0:
            continue
        profile.intent_ratios[intent] = profile.intent_ratios.get(intent, 0) + count

    # Merge nudge outcomes
    for signal, outcome in delta.get("nudge_outcomes", {}).items():
        if signal not in profile.nudge_outcomes:
            profile.nudge_outcomes[signal] = {"accepted": 0, "ignored": 0}
        if outcome in ("accepted", "ignored"):
            profile.nudge_outcomes[signal][outcome] += 1

    # Recompute bias adjustments
    profile.computed_bias_adjustments = compute_bias_adjustments(profile)


# ── Persistence ──────────────────────────────────────────────────────


def load_global_profile(ttl_days: int = PROFILE_TTL_DAYS) -> GlobalBehaviorProfile:
    """Load global profile from disk, or return fresh if absent/corrupt.

    Prunes the entire profile if it's older than ttl_days.
    """
    if not GLOBAL_PROFILE_PATH.exists():
        return GlobalBehaviorProfile()

    try:
        with open(GLOBAL_PROFILE_PATH) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return GlobalBehaviorProfile()

    if not isinstance(data, dict):
        return GlobalBehaviorProfile()

    profile = GlobalBehaviorProfile.from_dict(data)

    # TTL pruning: if profile is older than ttl_days, start fresh
    if ttl_days > 0:
        age_days = (time.time() - profile.updated_at) / 86400
        if age_days > ttl_days:
            return GlobalBehaviorProfile()

    return profile


def save_global_profile(profile: GlobalBehaviorProfile) -> None:
    """Save global profile to disk. Non-fatal on error."""
    GLOBAL_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(GLOBAL_PROFILE_PATH, "w") as f:
            json.dump(profile.to_dict(), f, indent=2)
    except OSError:
        pass  # Non-fatal — global memory is observability, not correctness

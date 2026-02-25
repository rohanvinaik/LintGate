"""History-aware coherence analysis for ControlPlane."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .session_memory import SessionMemory
    from .types import ChannelResult


_STATE_SEVERITY = {
    "stable": 0,
    "isolated": 1,
    "coupled": 2,
    "systemic": 3,
    "degraded": 4,
}


def state_severity(state: str) -> int:
    """Map coherence state to a severity integer for comparison."""
    return _STATE_SEVERITY.get(state, 0)


def detect_persistent_loud(
    session: SessionMemory,
    current_loud: list[str],
) -> list[tuple[str, int]]:
    """Detect channels that have been loud for 3+ consecutive runs.

    Returns list of (channel_name, streak_count) for persistent channels.
    """
    if not current_loud or len(session.snapshots) < 2:
        return []

    persistent: list[tuple[str, int]] = []

    for ch_name in current_loud:
        # Count consecutive recent snapshots where this channel was loud
        streak = 0
        for snap in reversed(session.snapshots):
            if ch_name in snap.loud_channels:
                streak += 1
            else:
                break

        # +1 for the current run (not yet in snapshots)
        total_streak = streak + 1

        if total_streak >= 3:
            persistent.append((ch_name, total_streak))

    return persistent


def detect_resolutions(
    session: SessionMemory,
    current_silent: list[str],
) -> list[str]:
    """Detect channels that were loud in the last run but are now silent.

    Only reports channels that were loud in the most recent snapshot,
    to avoid noise from channels that have been silent for a while.
    """
    if not session.snapshots:
        return []

    last_snapshot = session.snapshots[-1]
    last_loud = set(last_snapshot.loud_channels)
    current_silent_set = set(current_silent)

    return sorted(last_loud & current_silent_set)


# Known refactoring tradeoff pairs: (improved_kind, regressed_kind)
_TRADEOFF_PAIRS: list[tuple[str, str]] = [
    ("cyclomatic_complexity", "too_many_args"),
    ("cognitive_complexity", "too_many_args"),
    ("file_too_long", "too_many_functions"),
]


def detect_refactoring_tradeoffs(
    current_results: list[ChannelResult],
    session: SessionMemory,
) -> list[dict[str, object]]:
    """Detect refactoring tradeoff patterns between current and previous findings.

    Returns annotation dicts when a known tradeoff pair is detected:
    one metric improved while a correlated metric regressed.
    """
    if not session.snapshots:
        return []

    last_snapshot = session.snapshots[-1]
    if not last_snapshot.finding_index:
        return []

    # Count current findings by kind
    current_kinds: dict[str, int] = {}
    for cr in current_results:
        for f in cr.findings:
            kind = getattr(f, "kind", "") or ""
            if kind:
                current_kinds[kind] = current_kinds.get(kind, 0) + 1

    # Count previous findings by kind from finding_index
    prev_kinds: dict[str, int] = {}
    for _fp, summary in last_snapshot.finding_index.items():
        kind = summary.get("kind", "")
        count = int(summary.get("count", 1))
        if kind:
            prev_kinds[kind] = prev_kinds.get(kind, 0) + count

    tradeoffs: list[dict[str, object]] = []
    for improved_kind, regressed_kind in _TRADEOFF_PAIRS:
        prev_improved = prev_kinds.get(improved_kind, 0)
        curr_improved = current_kinds.get(improved_kind, 0)
        prev_regressed = prev_kinds.get(regressed_kind, 0)
        curr_regressed = current_kinds.get(regressed_kind, 0)

        # Tradeoff: improved count decreased AND regressed count increased
        if curr_improved < prev_improved and curr_regressed > prev_regressed:
            tradeoffs.append(
                {
                    "type": "refactor_tradeoff_detected",
                    "improved": improved_kind,
                    "improved_delta": curr_improved - prev_improved,
                    "regressed": regressed_kind,
                    "regressed_delta": curr_regressed - prev_regressed,
                }
            )

    return tradeoffs

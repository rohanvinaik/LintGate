"""Cognitive mode state and transition rules.

Three modes:
- NORMAL: default execution mode
- THEORY: compass exploration / theory extraction
- HABIT: sustained execution with compaction
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any

# ── Transition table ────────────────────────────────────────────────
# Source → Target: allowed?
# NORMAL → THEORY:  yes (enter exploration)
# THEORY → NORMAL:  yes (freeze or cancel)
# NORMAL → HABIT:   yes (habit detection)
# HABIT  → NORMAL:  yes (habit exit)
# THEORY → HABIT:   BLOCKED (must freeze/cancel first)
# HABIT  → THEORY:  BLOCKED (must exit habit first)

_ALLOWED_TRANSITIONS: dict[tuple[str, str], str] = {
    ("normal", "theory"): "normal->theory",
    ("theory", "normal"): "theory->normal",
    ("normal", "habit"): "normal->habit",
    ("habit", "normal"): "habit->normal",
}


class CognitiveMode(enum.Enum):
    """The three cognitive modes for the compass system."""

    NORMAL = "normal"
    THEORY = "theory"
    HABIT = "habit"


@dataclass
class ModeState:
    """Session-scoped cognitive mode state.

    Tracks the current mode, entry timestamp, and theory-mode metadata
    (frozen compass hash, exploration claims count).
    """

    current: CognitiveMode = CognitiveMode.NORMAL
    entered_at: float = 0.0
    theory_frozen: bool = False
    frozen_compass_hash: str = ""
    exploration_claims_added: int = 0

    def transition(self, target: CognitiveMode) -> str | None:
        """Attempt a mode transition.

        Returns the transition label string (e.g. "normal->theory") if
        the transition is allowed, or None if blocked.

        Transition rules:
        - Normal -> Theory: always allowed
        - Theory -> Normal: allowed (via freeze or cancel)
        - Normal -> Habit: allowed (via habit_mode detection)
        - Habit -> Normal: allowed (via habit exit)
        - Theory -> Habit: BLOCKED (must freeze first)
        - Habit -> Theory: BLOCKED (must exit habit first)
        """
        if self.current == target:
            return None

        key = (self.current.value, target.value)
        label = _ALLOWED_TRANSITIONS.get(key)
        if label is None:
            return None

        self.current = target
        self.entered_at = time.time()
        return label

    def enter_theory(self) -> str | None:
        """Transition into theory mode.

        Returns the transition label or None if blocked.
        Resets theory-mode metadata on entry.
        """
        label = self.transition(CognitiveMode.THEORY)
        if label is not None:
            self.theory_frozen = False
            self.frozen_compass_hash = ""
            self.exploration_claims_added = 0
        return label

    def freeze_theory(self, compass_hash: str) -> str | None:
        """Freeze the compass and return to normal mode.

        Records the frozen compass hash and marks theory as frozen.
        Returns the transition label or None if not in theory mode.
        """
        if self.current != CognitiveMode.THEORY:
            return None

        label = self.transition(CognitiveMode.NORMAL)
        if label is not None:
            self.theory_frozen = True
            self.frozen_compass_hash = compass_hash
        return label

    def cancel_theory(self) -> str | None:
        """Cancel theory mode without freezing.

        Returns the transition label or None if not in theory mode.
        Does not set theory_frozen or compass hash.
        """
        if self.current != CognitiveMode.THEORY:
            return None

        label = self.transition(CognitiveMode.NORMAL)
        if label is not None:
            self.theory_frozen = False
            self.frozen_compass_hash = ""
        return label

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for persistence."""
        return {
            "current": self.current.value,
            "entered_at": self.entered_at,
            "theory_frozen": self.theory_frozen,
            "frozen_compass_hash": self.frozen_compass_hash,
            "exploration_claims_added": self.exploration_claims_added,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModeState:
        """Deserialize from a plain dict."""
        if not data:
            return cls()
        mode_str = str(data.get("current", "normal"))
        try:
            mode = CognitiveMode(mode_str)
        except ValueError:
            mode = CognitiveMode.NORMAL
        return cls(
            current=mode,
            entered_at=float(data.get("entered_at", 0.0)),
            theory_frozen=bool(data.get("theory_frozen", False)),
            frozen_compass_hash=str(data.get("frozen_compass_hash", "")),
            exploration_claims_added=int(data.get("exploration_claims_added", 0)),
        )

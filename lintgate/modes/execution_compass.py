"""Execution compass — frozen directional state for runtime alignment checks.

Built from a CompassState at theory-freeze time. Provides keyword-based
alignment checking against away/forbidden directives and compact
serialization for compaction snapshots.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..compass import CompassState


@dataclass
class ExecutionCompass:
    """Frozen directional state derived from the compass.

    toward:    directives from the solution axis (goals to pursue)
    away:      directives to avoid (anti-patterns)
    forbidden: hard-blocked directives (enforceable rules)
    true_north: problem axis summary (one-sentence orientation)
    compass_hash: hash of the source CompassState at freeze time
    """

    toward: list[str] = field(default_factory=list)
    away: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    true_north: str = ""
    compass_hash: str = ""

    @classmethod
    def from_compass_state(cls, state: CompassState) -> ExecutionCompass:
        """Build an ExecutionCompass from a CompassState's directives and axes.

        Categorizes directives by kind (toward/away/forbidden) and
        extracts the problem axis summary as true_north.
        """
        toward: list[str] = []
        away: list[str] = []
        forbidden: list[str] = []

        for directive in state.directives:
            text = directive.text.strip()
            if not text:
                continue
            if directive.kind == "toward":
                toward.append(text)
            elif directive.kind == "away":
                away.append(text)
            elif directive.kind == "forbidden":
                forbidden.append(text)

        # Extract true_north from the problem axis summary
        true_north = ""
        problem_axis = state.axes.get("problem")
        if problem_axis and problem_axis.summary:
            true_north = problem_axis.summary

        # Compute hash from the source state
        compass_hash = state.frozen_hash if state.frozen_hash else ""

        return cls(
            toward=toward,
            away=away,
            forbidden=forbidden,
            true_north=true_north,
            compass_hash=compass_hash,
        )

    def check_alignment(self, action_sig: str) -> dict[str, Any]:
        """Check whether an action signature aligns with the compass.

        Performs case-insensitive keyword matching of the action_sig
        against away (warnings) and forbidden (violations) directives.

        Returns:
            {
                "aligned": bool,       # True if no violations
                "violations": [...],   # forbidden directives that matched
                "warnings": [...]      # away directives that matched
            }
        """
        action_lower = action_sig.lower()
        violations: list[str] = []
        warnings: list[str] = []

        for directive in self.forbidden:
            # Check if any significant word from the directive appears
            # in the action signature
            directive_lower = directive.lower()
            words = [w for w in directive_lower.split() if len(w) > 3]
            for word in words:
                if word in action_lower:
                    violations.append(directive)
                    break

        for directive in self.away:
            directive_lower = directive.lower()
            words = [w for w in directive_lower.split() if len(w) > 3]
            for word in words:
                if word in action_lower:
                    warnings.append(directive)
                    break

        return {
            "aligned": len(violations) == 0,
            "violations": violations,
            "warnings": warnings,
        }

    def check_alignment_with_specs(
        self, action_sig: str, specs: list | None = None
    ) -> dict[str, Any]:
        """Enhanced alignment: base compass check + PrescriptiveSpec forbidden behaviors."""
        result = self.check_alignment(action_sig)

        if not specs:
            return result

        action_lower = action_sig.lower()
        prescriptive_violations: list[str] = []
        for spec in specs:
            for fb in getattr(spec, "forbidden_behaviors", []):
                desc_lower = fb.description.lower()
                words = [w for w in desc_lower.split() if len(w) > 3]
                for word in words:
                    if word in action_lower:
                        prescriptive_violations.append(fb.description)
                        break

        if prescriptive_violations:
            result["aligned"] = False
            result.setdefault("violations", []).extend(prescriptive_violations)
            result["prescriptive_violations"] = prescriptive_violations

        return result

    def to_compact_json(self) -> str:
        """Serialize to a compact JSON string (~800 tokens).

        Suitable for injection into compaction snapshots.
        Truncates lists to keep output bounded.
        """
        compact = {
            "true_north": self.true_north[:200] if self.true_north else "",
            "toward": [t[:120] for t in self.toward[:8]],
            "away": [a[:120] for a in self.away[:8]],
            "forbidden": [f[:120] for f in self.forbidden[:8]],
            "hash": self.compass_hash,
        }
        return json.dumps(compact, separators=(",", ":"))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for persistence."""
        return {
            "toward": list(self.toward),
            "away": list(self.away),
            "forbidden": list(self.forbidden),
            "true_north": self.true_north,
            "compass_hash": self.compass_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionCompass:
        """Deserialize from a plain dict."""
        if not data:
            return cls()
        return cls(
            toward=list(data.get("toward", [])),
            away=list(data.get("away", [])),
            forbidden=list(data.get("forbidden", [])),
            true_north=str(data.get("true_north", "")),
            compass_hash=str(data.get("compass_hash", "")),
        )

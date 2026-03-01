"""Deterministic disposition enforcement engine for agent behavior guidance."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent


class DispositionEnforcer:
    """Enforces behavior dispositions based on event history and config.

    State is persisted in session.behavior_compass under the "enforcement" key.
    """

    def __init__(
        self,
        config: ControlPlaneConfig,
        session: Any = None,
    ):
        self.config = config
        self.session = session
        self._ensure_state()

    def _ensure_state(self) -> None:
        """Initialize enforcement state in session if missing."""
        if self.session is None or not hasattr(self.session, "behavior_compass"):
            self.state = {}
            return

        if "enforcement" not in self.session.behavior_compass:
            self.session.behavior_compass["enforcement"] = {
                "rules": {},
                "counters": {"events": 0},
                "flags": {"needs_lint": False},
            }
        self.state = self.session.behavior_compass["enforcement"]

    def evaluate(self, event: SupervisionEvent) -> tuple[str | None, str | None]:
        """Evaluate enforcement rules for the current event.

        Returns a tuple (nudge_message, rule_id) if a rule fires, else (None, None).
        """
        if not self.config.disposition_enforcement.enabled:
            return None, None

        # Update event counter
        self.state["counters"]["events"] = self.state["counters"].get("events", 0) + 1

        # Track ignore for previously fired rule
        last_fired = self.state["flags"].pop("last_fired_rule", None)
        if last_fired:
            tool = event.tool_name.lower()
            is_lint = tool in {"lint_files", "lint_project", "controlplane_run"}
            # If we nudged for lint but got something else, it's an ignore
            if (
                (last_fired == "edit_without_lint" and not is_lint)
                or (last_fired == "cadence_check" and tool != "controlplane_run")
                or (
                    last_fired == "bash_no_prediction"
                    and not self.config.inquiry.prediction_tracking
                )
            ):
                self._mark_ignore(last_fired)

        disposition = None
        rule_id = None

        # Rule 1: NUDGE_EDIT_WITHOUT_LINT
        disposition = self._check_edit_without_lint(event)
        if disposition:
            rule_id = "edit_without_lint"

        # Rule 2: NUDGE_BASH_WITHOUT_PREDICTION
        if not disposition:
            disposition = self._check_bash_without_prediction(event)
            if disposition:
                rule_id = "bash_no_prediction"

        # Rule 3: NUDGE_CONTROLPLANE_CADENCE
        if not disposition:
            disposition = self._check_controlplane_cadence(event)
            if disposition:
                rule_id = "cadence_check"

        # Post-evaluation: update flags for NEXT event
        self._update_post_event_flags(event)

        return disposition, rule_id

    def _check_edit_without_lint(self, event: SupervisionEvent) -> str | None:
        """Nudge if an edit was made but no linting followed."""
        if not self.config.disposition_enforcement.nudge_after_edit_without_lint:
            return None

        tool = event.tool_name.lower()
        is_lint = tool in {"lint_files", "lint_project", "controlplane_run"}

        # If we just linted, we don't nudge (the flag will be cleared in post-eval)
        if is_lint:
            return None

        # If we need a lint but didn't do one, and this isn't an edit tool itself
        is_edit = any(
            kw in tool for kw in ["write", "edit", "replace", "patch", "apply"]
        )

        if (
            self.state["flags"].get("needs_lint")
            and not is_edit
            and self._can_fire("edit_without_lint")
        ):
            prefix = self._get_nudge_prefix("edit_without_lint")
            self._mark_fired("edit_without_lint")
            return (
                f"{prefix}: You've made several edits without running validation. "
                "Run `lint_files` or `controlplane_run` to ensure no regressions."
            )

        return None

    def _check_bash_without_prediction(self, event: SupervisionEvent) -> str | None:
        """Nudge for prediction tracking when Bash is used."""
        if not self.config.disposition_enforcement.nudge_before_bash_without_prediction:
            return None

        if event.tool_name.lower() != "bash":
            return None

        if not self.config.inquiry.prediction_tracking and self._can_fire(
            "bash_no_prediction"
        ):
            prefix = self._get_nudge_prefix("bash_no_prediction")
            self._mark_fired("bash_no_prediction")
            return (
                f"{prefix}: You're running raw shell commands. To improve reasoning, "
                "consider enabling `controlplane.inquiry.prediction_tracking` "
                "to state your hypothesis before execution."
            )

        return None

    def _check_controlplane_cadence(self, event: SupervisionEvent) -> str | None:
        """Nudge for a health check after many events."""
        cadence = self.config.disposition_enforcement.cadence_health_check_events
        if cadence <= 0:
            return None

        events_since_last = self.state["counters"]["events"]

        if event.tool_name.lower() == "controlplane_run":
            self.state["counters"]["events"] = 0
            return None

        if events_since_last >= cadence and self._can_fire("cadence_check"):
            prefix = self._get_nudge_prefix("cadence_check")
            self._mark_fired("cadence_check")
            return (
                f"{prefix}: It's been {events_since_last} tool calls since your last "
                "comprehensive health check. Run `controlplane_run` to verify project stability."
            )

        return None

    def _get_nudge_prefix(self, rule_id: str) -> str:
        """Return an escalated prefix based on rule fire count."""
        rule_state = self.state["rules"].get(rule_id, {})
        fire_count = rule_state.get("fire_count", 0)
        if fire_count == 0:
            return "PROTIP"
        if fire_count == 1:
            return "IMPORTANT REMINDER"
        return "URGENT DISPOSITION"

    def _update_post_event_flags(self, event: SupervisionEvent) -> None:
        """Update flags and track compliance/ignores after evaluation."""
        tool = event.tool_name.lower()
        is_edit = any(
            kw in tool for kw in ["write", "edit", "replace", "patch", "apply"]
        )
        is_lint = tool in {"lint_files", "lint_project", "controlplane_run"}

        # Track compliance for edit_without_lint BEFORE clearing the flag
        if self.state["flags"].get("needs_lint") and is_lint:
            self._mark_compliance("edit_without_lint")

        # Update needs_lint flag
        if is_edit:
            self.state["flags"]["needs_lint"] = True
        elif is_lint:
            self.state["flags"]["needs_lint"] = False

    def _can_fire(self, rule_id: str) -> bool:
        """Check if a rule can fire based on fire count and max nudges."""
        rule_state = self.state["rules"].setdefault(
            rule_id, {"fire_count": 0, "compliance_count": 0, "ignore_count": 0}
        )
        max_nudges = self.config.disposition_enforcement.max_nudges_per_disposition
        return rule_state["fire_count"] < max_nudges

    def _mark_fired(self, rule_id: str) -> None:
        """Update fire count and timestamp for a rule."""
        import time

        rule_state = self.state["rules"].setdefault(
            rule_id, {"fire_count": 0, "compliance_count": 0, "ignore_count": 0}
        )
        rule_state["fire_count"] += 1
        rule_state["last_fired_at"] = time.time()
        self.state["flags"]["last_fired_rule"] = rule_id

    def _mark_compliance(self, rule_id: str) -> None:
        """Increment compliance count for a rule."""
        rule_state = self.state["rules"].setdefault(
            rule_id, {"fire_count": 0, "compliance_count": 0, "ignore_count": 0}
        )
        rule_state["compliance_count"] = rule_state.get("compliance_count", 0) + 1

    def _mark_ignore(self, rule_id: str) -> None:
        """Increment ignore count for a rule."""
        rule_state = self.state["rules"].setdefault(
            rule_id, {"fire_count": 0, "compliance_count": 0, "ignore_count": 0}
        )
        rule_state["ignore_count"] = rule_state.get("ignore_count", 0) + 1

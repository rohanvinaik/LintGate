"""Behavior tools — orthogonal behavioral supervision lenses.

Tools:
- hygiene_check: Command-class precondition checks (is venv active? lockfile fresh?)
- constraint_check: Constraint ledger, coverage gaps, uncertainty zones, similar failures
- prediction_register: Register falsifiable predictions for upcoming actions
- behavior_precheck: DEPRECATED compat wrapper — delegates to the three tools above
- global_memory_status: Cross-session behavioral analysis status
- global_memory_reset: Reset global behavior profile
"""

from __future__ import annotations

from mcp_tools._behavior_impl import (
    impl_behavior_precheck,
    impl_constraint_check,
    impl_global_memory_reset,
    impl_global_memory_status,
    impl_hygiene_check,
    impl_prediction_register,
)


def register(mcp, helpers):
    """Register behavior tools on the shared MCP instance."""

    @mcp.tool()
    def hygiene_check(path: str, planned_action: str) -> str:
        """Check command-class hygiene preconditions before executing.

        WHEN TO USE: Before running Bash commands that install packages,
        commit code, modify env files, or publish builds. Catches missing
        venv, stale lockfiles, unpinned versions, and staged secrets.

        Example: hygiene_check(path="/my/project", planned_action="pip install requests")

        Args:
            path: Project root path.
            planned_action: Free text describing the planned command.
        """
        return impl_hygiene_check(helpers, path, planned_action)

    @mcp.tool()
    def constraint_check(
        path: str,
        planned_action: str,
        known_constraints: list[str] | None = None,
    ) -> str:
        """Check planned action against the constraint ledger.

        WHEN TO USE: Before attempting a new approach or after failures.
        Declares your known constraints, then identifies coverage gaps,
        uncertainty zones, and similar past failures.

        Example: constraint_check(path="/my/project",
            planned_action="run pytest",
            known_constraints=["some tests may fail due to missing fixtures"])

        Args:
            path: Project root path.
            planned_action: Free text describing the planned action.
            known_constraints: Agent's self-reported constraints for this action.
        """
        return impl_constraint_check(helpers, path, planned_action, known_constraints)

    @mcp.tool()
    def prediction_register(
        path: str,
        planned_action: str,
        prediction: str,
        prediction_type: str,
        prediction_value: str | int,
    ) -> str:
        """Register a falsifiable prediction for an upcoming action.

        WHEN TO USE: Before running a command whose outcome matters.
        Register what you expect to happen — the system will check the
        prediction against the actual outcome and track accuracy.

        Example: prediction_register(path="/my/project",
            planned_action="pytest tests/",
            prediction="Tests will pass",
            prediction_type="exit_code",
            prediction_value=0)

        Args:
            path: Project root path.
            planned_action: Free text describing the planned action.
            prediction: Free-text description of expected outcome.
            prediction_type: "exit_code", "error_signature", or "stdout_contains".
            prediction_value: The expected value for the prediction.
        """
        return impl_prediction_register(
            helpers, path, planned_action, prediction, prediction_type, prediction_value,
        )

    @mcp.tool()
    def behavior_precheck(
        path: str,
        planned_action: str,
        known_constraints: list[str] | None = None,
        prediction: str | None = None,
        prediction_type: str | None = None,
        prediction_value: str | int | None = None,
    ) -> str:
        """Check a planned action against known constraints before executing it.

        DEPRECATED: Prefer hygiene_check, constraint_check, prediction_register.

        Example: behavior_precheck(path="/my/project", planned_action="run pytest",
            known_constraints=["some tests may fail due to missing fixtures"])

        Args:
            path: Project root path.
            planned_action: Free text describing the planned action.
            known_constraints: Agent's self-reported constraints for this action.
            prediction: Optional free-text description of expected outcome.
            prediction_type: "exit_code", "error_signature", or "stdout_contains".
            prediction_value: The expected value for the prediction.
        """
        _tools = {
            "constraint_check": constraint_check,
            "prediction_register": prediction_register,
            "hygiene_check": hygiene_check,
        }
        return impl_behavior_precheck(
            helpers, _tools, path, planned_action,
            known_constraints, prediction, prediction_type, prediction_value,
        )

    @mcp.tool()
    def global_memory_status(path: str) -> str:
        """Show cross-session behavioral analysis status.

        Returns session count, learned patterns, calibration settings,
        and computed bias adjustments from accumulated behavioral data.

        Args:
            path: Project root path.
        """
        return impl_global_memory_status(helpers, path)

    @mcp.tool()
    def global_memory_reset(path: str) -> str:
        """Reset the global behavior profile. Useful after major workflow changes.

        Args:
            path: Project root path.
        """
        return impl_global_memory_reset(helpers, path)

    return {
        "hygiene_check": hygiene_check,
        "constraint_check": constraint_check,
        "prediction_register": prediction_register,
        "behavior_precheck": behavior_precheck,
        "global_memory_status": global_memory_status,
        "global_memory_reset": global_memory_reset,
    }

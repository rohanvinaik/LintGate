"""Dependency channel — supervision for dependency health.

Wraps existing dependency_health module and adds ControlPlane integration.
Checks lockfile freshness, manifest quality, and dependency conflicts.

Advisory by default — dependency issues produce warnings/informational,
not blocking errors.
"""

from __future__ import annotations

import time
from typing import Any

from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    RepairAction,
    SupervisionEvent,
)
from lintgate.types import LintIssue


class DependencyChannel:
    """Supervision channel for dependency health.

    Reuses existing dependency_health module for actual checks.
    Wraps results into ChannelResult format.
    """

    name = "deps"
    timeout_ms = 5000
    blocking_capable = False  # Advisory

    def should_run(self, event: SupervisionEvent, config: ControlPlaneConfig) -> bool:
        """Run on dependency, build, and config changes."""
        if event.surface == "mcp":
            return True
        classification = event.change_classification
        if classification is None:
            return False
        return classification.change_kind in ("dependency", "build", "config")

    def execute(self, event: SupervisionEvent, config: ControlPlaneConfig) -> ChannelResult:
        """Execute dependency health checks."""
        start = time.perf_counter()
        findings: list[LintIssue] = []
        repairs: list[RepairAction] = []

        project_root = event.project_root
        classification = event.change_classification

        if event.surface == "hook":
            # Fast path: quick checks for PostToolUse hook
            findings, repairs = self._quick_check(
                project_root,
                classification.change_kind if classification else "config",
                event.raw_input,
            )
        else:
            # Full check: comprehensive health audit for MCP/CI
            findings, repairs = self._full_check(project_root)

        elapsed_ms = (time.perf_counter() - start) * 1000
        status = "fail" if findings else "pass"
        severity = _max_severity(findings)

        return ChannelResult(
            channel=self.name,
            status=status,
            severity=severity,
            findings=findings,
            repairs=repairs,
            metrics={
                "issue_count": len(findings),
                "repair_count": len(repairs),
                "surface": event.surface,
            },
            duration_ms=elapsed_ms,
        )

    def _quick_check(
        self,
        project_root: str,
        change_kind: str,
        tool_input: dict[str, Any],
    ) -> tuple[list[LintIssue], list[RepairAction]]:
        """Fast dependency check for hook context (<5ms target)."""
        findings: list[LintIssue] = []
        repairs: list[RepairAction] = []

        try:
            from lintgate.dependency_health import quick_dependency_check

            warnings = quick_dependency_check(project_root, change_kind, tool_input)

            for warning in warnings:
                findings.append(
                    LintIssue(
                        linter="dep_channel",
                        kind="dep_warning",
                        message=warning,
                        severity="informational",
                    )
                )

                # Propose repair for lockfile issues
                if "lockfile" in warning.lower() or "lock" in warning.lower():
                    repairs.append(
                        RepairAction(
                            channel="deps",
                            kind="command",
                            summary="Run: uv lock",
                            payload={"command": "uv lock", "cwd": project_root},
                            safe=True,
                        )
                    )
        except ImportError:
            pass  # dependency_health module not available
        except Exception:
            pass  # Non-fatal

        return findings, repairs

    def _full_check(
        self,
        project_root: str,
    ) -> tuple[list[LintIssue], list[RepairAction]]:
        """Comprehensive dependency health check for MCP/CI."""
        findings: list[LintIssue] = []
        repairs: list[RepairAction] = []

        try:
            from lintgate.dependency_health import full_dependency_health

            result = full_dependency_health(project_root)

            if not isinstance(result, dict):
                return findings, repairs

            # Process issues from health check.
            # Schema: each issue dict has "name", "status" (ok/warning/error),
            # "message", and optional "suggestion" string.
            status_to_severity = {
                "error": "warning",
                "warning": "informational",
                "info": "informational",
            }
            for issue in result.get("issues", []):
                findings.append(
                    LintIssue(
                        linter="dep_channel",
                        kind=issue.get("name", "dep_issue"),
                        message=issue.get("message", "Dependency issue"),
                        severity=status_to_severity.get(
                            issue.get("status", "info"), "informational"
                        ),
                    )
                )

                # Each issue may carry its own suggestion string
                suggestion = issue.get("suggestion")
                if suggestion:
                    repairs.append(
                        RepairAction(
                            channel="deps",
                            kind="command",
                            summary=suggestion,
                            payload={"command": suggestion, "cwd": project_root},
                            safe=True,
                        )
                    )

        except ImportError:
            pass
        except Exception:
            pass

        return findings, repairs


def _max_severity(findings: list[LintIssue]) -> str:
    """Get the highest severity from a list of findings."""
    if not findings:
        return "none"
    severity_order = {"blocking": 3, "warning": 2, "informational": 1, "none": 0}
    max_sev = max(findings, key=lambda f: severity_order.get(f.severity, 0))
    return max_sev.severity

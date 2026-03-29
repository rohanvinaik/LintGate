"""MCP tools for test hygiene analysis.

Provides on-demand test hygiene scanning via the test_hygiene_scan tool.
"""

from __future__ import annotations

from typing import Any

from mcp_tools._disk_helpers import tool_response


def register(mcp: Any, helpers: Any) -> dict[str, Any]:
    """Register test hygiene tools on the shared MCP instance."""

    @mcp.tool()
    def test_hygiene_scan(
        path: str,
        file_filter: str | None = None,
        include_repairs: bool = True,
    ) -> str:
        """Scan test suite for hygiene issues: stubs, weak-only assertions, duplicates.

        WHEN TO USE: After editing tests or during project health checks.
        Detects test waste that contributes zero specification value.

        Args:
            path: Project root path.
            file_filter: Optional substring filter for test file paths.
            include_repairs: Whether to include safe deletion proposals.
        """
        from lintgate.channels.test_hygiene_channel import TestHygieneChannel
        from lintgate.controlplane.types import (
            ChannelConfig,
            ControlPlaneConfig,
            SupervisionEvent,
        )

        settings: dict[str, Any] = {}
        if file_filter:
            settings["file_filter"] = file_filter

        config = ControlPlaneConfig(
            enabled=True,
            channels={"test_hygiene": ChannelConfig(enabled=True, settings=settings)},
        )
        event = SupervisionEvent(
            surface="mcp",
            project_root=path,
        )

        channel = TestHygieneChannel()
        result = channel.execute(event, config)

        output: dict[str, Any] = {
            "status": result.status,
            "severity": result.severity,
            "metrics": result.metrics,
            "findings": [
                {
                    "code": f.kind,
                    "message": f.message,
                    "file": f.file,
                    "line": f.line,
                    "severity": f.severity,
                    "confidence": f.confidence,
                    "evidence": f.evidence,
                }
                for f in result.findings
            ],
            "duration_ms": round(result.duration_ms, 1),
        }

        if include_repairs and result.repairs:
            output["repairs"] = [
                {
                    "action_id": r.action_id,
                    "kind": r.kind,
                    "summary": r.summary,
                    "safe": r.safe,
                    "payload": r.payload,
                }
                for r in result.repairs
            ]

        output["next_actions"] = _next_actions(result, path)

        project_root = helpers["_validate_project_root"](path)
        n_findings = len(output.get("findings", []))
        n_repairs = len(output.get("repairs", []))
        summary = f"Test hygiene: {n_findings} issues found. {n_repairs} repairs available."
        return tool_response(
            output, "test_hygiene_scan", project_root, summary,
            next_actions=output.get("next_actions"),
        )

    return {"test_hygiene_scan": test_hygiene_scan}


def _next_actions(result: Any, path: str) -> list[dict[str, Any]]:
    """Suggest follow-up actions based on findings."""
    actions: list[dict[str, Any]] = []

    if result.repairs:
        actions.append(
            {
                "tool": "controlplane_apply_repairs",
                "args": {"path": path},
                "reason": f"Apply {len(result.repairs)} safe deletion(s)",
                "priority": 5,
            }
        )

    if any(f.kind == "THYGIENE001" for f in result.findings):
        actions.append(
            {
                "tool": "mutation_prescribe_tests",
                "args": {"path": path},
                "reason": "Replace stubs with real tests",
                "priority": 4,
            }
        )

    if any(f.kind == "THYGIENE002" for f in result.findings):
        actions.append(
            {
                "tool": "analyze_test_strength",
                "args": {"path": path},
                "reason": "Assess weak-assertion functions for mutation vulnerability",
                "priority": 3,
            }
        )

    return actions

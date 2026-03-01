"""Deterministic remediation router for finding->next_action mapping."""

from __future__ import annotations

from typing import Any


def route_finding(finding: dict[str, Any], channel: str) -> dict[str, Any]:
    """Route a finding to a deterministic next action based on its kind and channel."""
    kind = finding.get("kind", "unknown")
    severity = finding.get("severity", "unknown")
    target_file = finding.get("file") or finding.get("path") or "unknown"
    # Base response template
    route = {
        "tool": "controlplane_get_details",
        "args": {},
        "rationale": "Unknown finding classification. Drill down for details.",
        "remediation_sequence": [
            {"step": 1, "action": "Review finding details and context."}
        ],
    }

    # 1. Complexity findings
    if "complexity" in kind.lower():
        route.update(
            {
                "tool": "lint_files",
                "args": {"files": [target_file]} if target_file != "unknown" else {},
                "rationale": "High complexity detected. Consider refactoring into smaller, testable functions.",
                "remediation_sequence": [
                    {
                        "step": 1,
                        "action": "Extract long code blocks into helper functions.",
                    },
                    {"step": 2, "action": "Verify refactor with lint_files."},
                ],
            }
        )

    # 2. File-too-long findings
    elif "file-too-long" in kind.lower() or "too-many-lines" in kind.lower():
        route.update(
            {
                "tool": "lint_files",
                "args": {"files": [target_file]} if target_file != "unknown" else {},
                "rationale": "File exceeded length thresholds. High risk of maintenance debt.",
                "remediation_sequence": [
                    {
                        "step": 1,
                        "action": "Identify logical components to move to new files.",
                    },
                    {"step": 2, "action": "Split file and update imports."},
                ],
            }
        )

    # 3. Import / Undefined-name / Syntax findings
    elif any(
        k in kind.lower() for k in ["import", "undefined", "syntax", "name-error"]
    ):
        route.update(
            {
                "tool": "lint_files",
                "args": {"files": [target_file]} if target_file != "unknown" else {},
                "rationale": "Basic code integrity issue detected (import error or undefined name).",
                "remediation_sequence": [
                    {
                        "step": 1,
                        "action": f"Fix missing import or typo in {target_file}.",
                    },
                    {"step": 2, "action": "Run lint_files to confirm resolution."},
                ],
            }
        )

    # 4. Duplicate-definition findings
    elif "duplicate" in kind.lower():
        route.update(
            {
                "tool": "lint_files",
                "args": {"files": [target_file]} if target_file != "unknown" else {},
                "rationale": "Duplicate definition found. May cause runtime confusion or shadowed logic.",
                "remediation_sequence": [
                    {
                        "step": 1,
                        "action": "Remove redundant definition or rename to avoid collision.",
                    }
                ],
            }
        )

    # 5. Test failure findings (Mesh context)
    elif channel == "test" or "test_failure" in kind.lower():
        route.update(
            {
                "tool": "controlplane_get_details",
                "args": {"channel": "test"},
                "rationale": "Mesh detected test regressions. Full drill-down required to see tracebacks.",
                "remediation_sequence": [
                    {
                        "step": 1,
                        "action": "Review failing test output in controlplane_get_details.",
                    },
                    {"step": 2, "action": "Fix regression and re-run tests."},
                ],
            }
        )

    # 6. Security findings
    elif (
        channel == "security"
        or severity == "blocking"
        and any(k in kind.lower() for k in ["security", "bandit", "vulnerability"])
    ):
        route.update(
            {
                "tool": "controlplane_run",
                "args": {"strictness": "strict", "channels": "lint"},
                "rationale": "Security-sensitive finding. Escalating to strict-mode scan.",
                "remediation_sequence": [
                    {
                        "step": 1,
                        "action": "Review vulnerability detail and OWASP context.",
                    },
                    {
                        "step": 2,
                        "action": "Apply security patch and run strict health check.",
                    },
                ],
            }
        )

    return route

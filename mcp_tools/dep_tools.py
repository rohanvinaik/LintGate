"""Dependency health tools — dep_health_check, dep_sync, toolchain_health_check."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def register(mcp, helpers):
    """Register dependency tools on the shared MCP instance."""

    @mcp.tool()
    def dep_health_check(path: str) -> str:
        """Run a comprehensive dependency health audit for a project.

        Checks virtual environment, lockfile presence and freshness,
        .python-version, conflicting package managers, manifest quality,
        and dependency churn patterns.

        Returns a structured report with issues and suggestions.
        """
        from lintgate.dependency_health import full_dependency_health

        project_root = helpers["_validate_project_root"](path)
        return json.dumps(full_dependency_health(project_root), indent=2)

    @mcp.tool()
    def dep_sync(
        path: str,
        create_venv: bool = False,
        lock: bool = False,
    ) -> str:
        """Check dependency sync status and optionally create venv or lockfile.

        By default, only reports status. Use flags to take action:
        - create_venv: Create a .venv with `uv venv .venv`
        - lock: Generate/refresh lockfile with `uv lock`

        Returns sync status and any actions taken.
        """
        import shutil
        import subprocess

        project_root = helpers["_validate_project_root"](path)
        root = Path(project_root)
        result: dict[str, Any] = {"project": project_root, "actions": []}

        # Check current state
        from lintgate.dependency_health import full_dependency_health

        health = full_dependency_health(project_root)
        result["health_before"] = health["summary"]

        uv_path = shutil.which("uv")
        if not uv_path:
            result["error"] = (
                "uv not found in PATH — install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
            )
            return json.dumps(result, indent=2)

        if create_venv:
            venv_path = root / ".venv"
            if venv_path.exists():
                result["actions"].append(
                    {
                        "action": "create_venv",
                        "status": "skipped",
                        "reason": ".venv already exists",
                    }
                )
            else:
                try:
                    proc = subprocess.run(
                        [uv_path, "venv", ".venv"],
                        capture_output=True,
                        text=True,
                        timeout=60,
                        cwd=project_root,
                    )
                    result["actions"].append(
                        {
                            "action": "create_venv",
                            "status": "ok" if proc.returncode == 0 else "error",
                            "returncode": proc.returncode,
                            "stderr": proc.stderr.strip()[-500:]
                            if proc.stderr
                            else None,
                        }
                    )
                except subprocess.TimeoutExpired:
                    result["actions"].append(
                        {"action": "create_venv", "status": "timeout"}
                    )

        if lock:
            try:
                proc = subprocess.run(
                    [uv_path, "lock"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=project_root,
                )
                result["actions"].append(
                    {
                        "action": "lock",
                        "status": "ok" if proc.returncode == 0 else "error",
                        "returncode": proc.returncode,
                        "stderr": proc.stderr.strip()[-500:] if proc.stderr else None,
                    }
                )
            except subprocess.TimeoutExpired:
                result["actions"].append({"action": "lock", "status": "timeout"})

        # Re-check health after actions
        if create_venv or lock:
            health_after = full_dependency_health(project_root)
            result["health_after"] = health_after["summary"]

        return json.dumps(result, indent=2)

    @mcp.tool()
    def toolchain_health_check(
        path: str,
        install_missing: bool = False,
    ) -> str:
        """Check CLI tool availability against the toolchain manifest.

        WHEN TO USE: At session start or when tools are missing. This checks
        the toolchain layer (CLI tools LintGate depends on), not project
        dependencies — use dep_health_check for project deps.

        Reads tool requirements from gate_contract.yaml, discovers what's
        installed, and reports gaps with install hints. Self-managing:
        warns when a linter's required_tool isn't in the manifest.

        Args:
            path: Project root path.
            install_missing: Auto-install missing tools marked auto_install=true.
        """
        from lintgate.tool_manifest import (
            full_toolchain_report,
            install_missing_tools,
        )

        project_root = helpers["_validate_project_root"](path)
        report = full_toolchain_report(project_root)

        output: dict[str, Any] = {
            "summary": report.summary,
            "all_required_met": report.all_required_met,
            "tools": [],
            "drift_warnings": report.drift_warnings,
        }

        for s in report.tools:
            tool_info: dict[str, Any] = {
                "id": s.id,
                "installed": s.installed,
                "required": s.requirement.required,
                "kind": s.requirement.kind,
            }
            if s.installed:
                tool_info["version"] = s.version
                tool_info["location"] = s.location
            else:
                tool_info["install_hint"] = s.install_hint
            output["tools"].append(tool_info)

        if install_missing:
            results = install_missing_tools(project_root, report.tools, auto_only=True)
            output["install_results"] = results

        return json.dumps(output, indent=2)

    return {
        "dep_health_check": dep_health_check,
        "dep_sync": dep_sync,
        "toolchain_health_check": toolchain_health_check,
    }

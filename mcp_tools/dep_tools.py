"""Dependency health tools — dep_health_check, dep_sync."""

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
                    {"action": "create_venv", "status": "skipped", "reason": ".venv already exists"}
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
                            "stderr": proc.stderr.strip()[-500:] if proc.stderr else None,
                        }
                    )
                except subprocess.TimeoutExpired:
                    result["actions"].append({"action": "create_venv", "status": "timeout"})

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

    return {
        "dep_health_check": dep_health_check,
        "dep_sync": dep_sync,
    }

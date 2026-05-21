"""Dependency health tools — thin subprocess wrappers around scripts/dep_check.py.

All computation lives in scripts/dep_check.py. This module registers MCP
tools that invoke the script via subprocess and relay its stdout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "dep_check.py",
)


def _run_script(path: str, *args: str) -> str:
    """Invoke scripts/dep_check.py as a subprocess and return its stdout.

    The script already emits a slim JSON envelope — we relay verbatim.
    On non-zero exit or non-JSON stdout, returns an error envelope.
    """
    try:
        proc = subprocess.run(
            [sys.executable, _SCRIPT, path, *args],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "dep_check subprocess timed out"})
    except OSError as exc:
        return json.dumps({"error": f"dep_check subprocess failed: {exc}"})

    stdout = (proc.stdout or "").strip()
    if proc.returncode != 0 and not stdout:
        return json.dumps({
            "error": f"dep_check exit {proc.returncode}",
            "stderr": (proc.stderr or "").strip()[-500:],
        })
    return stdout or json.dumps({"error": "dep_check produced no output"})


def register(mcp, helpers):
    """Register dependency tools on the shared MCP instance."""
    del helpers  # unused — validation happens in the script

    @mcp.tool()
    def dep_health_check(path: str) -> str:
        """Run a comprehensive dependency health audit for a project.

        Checks virtual environment, lockfile presence and freshness,
        .python-version, conflicting package managers, manifest quality,
        and dependency churn patterns.

        Returns a structured report with issues and suggestions.
        """
        return _run_script(path, "health")

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
        args = ["sync"]
        if create_venv:
            args.append("--create-venv")
        if lock:
            args.append("--lock")
        return _run_script(path, *args)

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
        args = ["toolchain"]
        if install_missing:
            args.append("--install-missing")
        return _run_script(path, *args)

    return {
        "dep_health_check": dep_health_check,
        "dep_sync": dep_sync,
        "toolchain_health_check": toolchain_health_check,
    }

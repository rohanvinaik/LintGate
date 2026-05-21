"""Behavior tools — thin subprocess wrappers around scripts/behavior_check.py.

All computation lives in scripts/behavior_check.py. This module registers MCP
tools that invoke the script via subprocess and relay its stdout.

Tools:
- hygiene_check, constraint_check, prediction_register (orthogonal)
- behavior_precheck (deprecated aggregator)
- global_memory_status, global_memory_reset
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "behavior_check.py",
)


def _run_script(*args: str) -> str:
    """Invoke scripts/behavior_check.py as a subprocess and relay stdout."""
    try:
        proc = subprocess.run(
            [sys.executable, _SCRIPT, *args],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "behavior_check subprocess timed out"})
    except OSError as exc:
        return json.dumps({"error": f"behavior_check subprocess failed: {exc}"})

    stdout = (proc.stdout or "").strip()
    if proc.returncode != 0 and not stdout:
        return json.dumps({
            "error": f"behavior_check exit {proc.returncode}",
            "stderr": (proc.stderr or "").strip()[-500:],
        })
    return stdout or json.dumps({"error": "behavior_check produced no output"})


def register(mcp, helpers):
    """Register behavior tools on the shared MCP instance."""
    del helpers  # unused — validation happens in the script

    @mcp.tool()
    def hygiene_check(path: str, planned_action: str) -> str:
        """Check command-class hygiene preconditions before executing.

        WHEN TO USE: Before running Bash commands that install packages,
        commit code, modify env files, or publish builds. Catches missing
        venv, stale lockfiles, unpinned versions, and staged secrets.
        """
        return _run_script("hygiene", path, "--action", planned_action)

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
        """
        args = ["constraint", path, "--action", planned_action]
        for kc in known_constraints or []:
            args.extend(["--known-constraint", kc])
        return _run_script(*args)

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
        Registers what you expect — the system checks against actual outcome.
        """
        return _run_script(
            "predict",
            path,
            "--action", planned_action,
            "--prediction", prediction,
            "--type", prediction_type,
            "--value", str(prediction_value),
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
        """DEPRECATED: Prefer hygiene_check, constraint_check, prediction_register."""
        args = ["precheck", path, "--action", planned_action]
        for kc in known_constraints or []:
            args.extend(["--known-constraint", kc])
        if prediction is not None:
            args.extend(["--prediction", prediction])
        if prediction_type is not None:
            args.extend(["--type", prediction_type])
        if prediction_value is not None:
            args.extend(["--value", str(prediction_value)])
        return _run_script(*args)

    @mcp.tool()
    def global_memory_status(path: str) -> str:
        """Show cross-session behavioral analysis status."""
        return _run_script("memory-status", path)

    @mcp.tool()
    def global_memory_reset(path: str) -> str:
        """Reset the global behavior profile."""
        return _run_script("memory-reset", path)

    return {
        "hygiene_check": hygiene_check,
        "constraint_check": constraint_check,
        "prediction_register": prediction_register,
        "behavior_precheck": behavior_precheck,
        "global_memory_status": global_memory_status,
        "global_memory_reset": global_memory_reset,
    }

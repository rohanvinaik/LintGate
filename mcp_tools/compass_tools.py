"""Compass tools — thin subprocess wrappers around scripts/compass_manage.py.

All computation lives in scripts/compass_manage.py; helper functions and
_impl_* compute live in lintgate/compass_helpers.py. This module registers
MCP tools that invoke the script via subprocess and relays stdout.

Helper re-exports preserve the import contract used by tests.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from lintgate.compass_helpers import (  # re-exported for tests
    _apply_answers,
    _build_hooks_config,
    _deep_merge,
    _impl_check,
    _impl_interview,
    _impl_reset,
    _impl_setup_hooks,
    _impl_status,
    _impl_theory_enter,
    _impl_theory_freeze,
    _impl_update,
    _load_mode_dict,
    _load_mode_obj,
    _merge_interviewed_claims,
    _refresh_axis_scores,
    _render_targets,
    _save_mode,
)

__all__ = [
    "_apply_answers",
    "_build_hooks_config",
    "_deep_merge",
    "_impl_check",
    "_impl_interview",
    "_impl_reset",
    "_impl_setup_hooks",
    "_impl_status",
    "_impl_theory_enter",
    "_impl_theory_freeze",
    "_impl_update",
    "_load_mode_dict",
    "_load_mode_obj",
    "_merge_interviewed_claims",
    "_refresh_axis_scores",
    "_render_targets",
    "_save_mode",
    "register",
]

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "compass_manage.py",
)


def _run_script(*args: str) -> str:
    """Invoke scripts/compass_manage.py as a subprocess and relay stdout."""
    try:
        proc = subprocess.run(
            [sys.executable, _SCRIPT, *args],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "compass_manage subprocess timed out"})
    except OSError as exc:
        return json.dumps({"error": f"compass_manage subprocess failed: {exc}"})

    stdout = (proc.stdout or "").strip()
    if proc.returncode != 0 and not stdout:
        return json.dumps({
            "error": f"compass_manage exit {proc.returncode}",
            "stderr": (proc.stderr or "").strip()[-500:],
        })
    return stdout or json.dumps({"error": "compass_manage produced no output"})


def register(mcp, helpers):
    """Register compass tools on the shared MCP instance."""
    del helpers  # unused — validation happens in the script

    @mcp.tool()
    def compass_status(path: str) -> str:
        """Show compass axes, depths, gap report, staleness, and cognitive mode."""
        return _run_script("status", path)

    @mcp.tool()
    def compass_check(path: str, action: str) -> str:
        """Check an action against toward/away/forbidden directives."""
        return _run_script("check", path, "--action", action)

    @mcp.tool()
    def compass_update(
        path: str,
        targets: list[str] | None = None,
        write: bool = False,
    ) -> str:
        """Re-extract compass from project docs and optionally render context files."""
        args = ["update", path]
        for t in targets or []:
            args.extend(["--target", t])
        if write:
            args.append("--write")
        return _run_script(*args)

    @mcp.tool()
    def compass_interview(
        path: str,
        answers: dict[str, str] | None = None,
        skip: bool = False,
    ) -> str:
        """Gap-filling interview — returns questions or applies answers."""
        args = ["interview", path]
        for k, v in (answers or {}).items():
            args.extend(["--answer", f"{k}={v}"])
        if skip:
            args.append("--skip")
        return _run_script(*args)

    @mcp.tool()
    def compass_reset(path: str, scope: str = "compass", confirm: bool = False) -> str:
        """Scoped state reset with dry-run default."""
        args = ["reset", path, "--scope", scope]
        if confirm:
            args.append("--confirm")
        return _run_script(*args)

    @mcp.tool()
    def theory_mode_enter(path: str) -> str:
        """Enter theory exploration mode. Normal->Theory allowed; Habit->Theory blocked."""
        return _run_script("theory-enter", path)

    @mcp.tool()
    def theory_mode_freeze(path: str) -> str:
        """Freeze compass and exit theory mode to normal."""
        return _run_script("theory-freeze", path)

    @mcp.tool()
    def setup_hooks(path: str, write: bool = False) -> str:
        """Generate .claude/settings.json hook configuration for compass hooks."""
        args = ["setup-hooks", path]
        if write:
            args.append("--write")
        return _run_script(*args)

    return {
        "compass_status": compass_status,
        "compass_check": compass_check,
        "compass_update": compass_update,
        "compass_interview": compass_interview,
        "compass_reset": compass_reset,
        "theory_mode_enter": theory_mode_enter,
        "theory_mode_freeze": theory_mode_freeze,
        "setup_hooks": setup_hooks,
    }

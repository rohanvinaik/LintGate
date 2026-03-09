"""Dependency health monitoring for LLM coding agents.

Detects unhealthy dependency management patterns:
- No virtual environment in a project
- Missing lockfile (manifest exists but no lock)
- Stale lockfile (manifest newer than lockfile)
- Global installs outside a venv
- Conflicting package manager artifacts
- Missing .python-version
- Dependency churn (repeated dep modifications in short windows)

Two entry points:
- quick_dependency_check(): Fast hook path (~5ms), returns warning strings
- full_dependency_health(): Thorough MCP path, returns structured dict
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

# ─── Re-exports from sub-modules ─────────────────────────────────────────
# All names that were previously defined here are re-exported so that
# existing ``from lintgate.dependency_health import X`` and
# ``mock.patch("lintgate.dependency_health.X")`` continue to work.
from lintgate._dep_health_checks import (  # noqa: F401
    _check_conflicting_managers,
    _check_dep_churn,
    _check_lockfile_freshness,
    _check_lockfiles,
    _check_manifest_health,
    _check_python_version_file,
    _check_venv,
    _find_unpinned_deps,
)
from lintgate._dep_health_helpers import (  # noqa: F401
    _CONFLICTING_COMBOS,
    _GLOBAL_INSTALL_PATTERN,
    _LOCK_TO_MANIFEST,
    _LOCKFILES,
    _MANIFEST_TO_LOCK,
    _MANIFESTS,
    _VENV_DIRS,
    _VENV_INDICATOR_FLAGS,
    DEP_HEALTH_DIR,
    HealthCheck,
    _find_venv,
    _format_duration,
    _has_ci_config,
    _has_python_project,
    _is_global_install,
    _load_dep_history,
    _load_json,
    _missing_lockfiles,
    _record_dep_event,
    _stale_lockfiles,
)

# ─── Quick hook path ──────────────────────────────────────────────────────


def quick_dependency_check(
    project_root: str,
    change_kind: str,
    tool_input: dict,
) -> list[str]:
    """Fast dependency health check for the PostToolUse hook path.

    Called when change_kind is "dependency" or "build".
    Returns a list of short warning strings for systemMessage.
    Budget: < 5ms (filesystem stat calls only, no subprocess).
    """
    warnings: list[str] = []
    root = Path(project_root)

    # 1. No venv detected
    if _has_python_project(root) and not _find_venv(root):
        warnings.append(
            "No virtual environment detected in project. Run `uv venv .venv` to create one."
        )

    # 2. Missing lockfile
    if change_kind == "dependency":
        missing = _missing_lockfiles(root)
        for manifest, locks in missing:
            lock_names = " or ".join(locks)
            warnings.append(
                f"{manifest} has no lockfile ({lock_names}). "
                f"Run `uv lock` or equivalent to pin versions."
            )

    # 3. Stale lockfile
    stale = _stale_lockfiles(root)
    for manifest, lock in stale:
        warnings.append(
            f"{lock} is older than {manifest} — lockfile may be out of sync. "
            f"Run `uv lock` to refresh."
        )

    # 4. Global install outside venv
    if change_kind == "build":
        command = tool_input.get("command", "")
        if _is_global_install(command, root):
            warnings.append(
                "Package install detected outside a virtual environment. "
                "Activate a venv first or use `uv pip install` inside one."
            )

    # 5. Track dep churn (non-blocking, best-effort)
    try:
        churn = _record_dep_event(project_root, change_kind)
        if churn and churn.get("is_churning"):
            count = churn["recent_count"]
            warnings.append(
                f"Dependency churn detected: {count} dep changes in the last 10 minutes. "
                f"Consider stabilizing before continuing."
            )
    except Exception:
        pass

    return warnings


# ─── Full MCP path ────────────────────────────────────────────────────────


def full_dependency_health(project_root: str) -> dict[str, Any]:
    """Comprehensive dependency health audit for MCP tool invocation.

    No time budget — runs all checks including subprocess calls.
    """
    root = Path(project_root)
    checks: list[HealthCheck] = []

    # 1. Virtual environment
    checks.append(_check_venv(root))

    # 2. Lockfile presence
    checks.extend(_check_lockfiles(root))

    # 3. Lockfile freshness
    checks.extend(_check_lockfile_freshness(root))

    # 4. Python version file
    checks.append(_check_python_version_file(root))

    # 5. Conflicting package managers
    checks.extend(_check_conflicting_managers(root))

    # 6. Manifest health (pyproject.toml has required fields)
    checks.extend(_check_manifest_health(root))

    # 7. Dep churn history
    checks.append(_check_dep_churn(project_root))

    all_checks = [c.to_dict() for c in checks]
    issues = [c for c in all_checks if c["status"] != "ok"]

    return {
        "project": project_root,
        "timestamp": time.time(),
        "healthy": len(issues) == 0,
        "checks": all_checks,
        "issues": issues,
        "issue_count": len(issues),
        "check_count": len(all_checks),
        "summary": _build_summary(checks),
    }


# ─── Summary ──────────────────────────────────────────────────────────────


def _build_summary(checks: list[HealthCheck]) -> dict[str, Any]:
    """Build a compact summary from check results."""
    ok = sum(1 for c in checks if c.status == "ok")
    warnings = sum(1 for c in checks if c.status == "warning")
    errors = sum(1 for c in checks if c.status == "error")

    if errors > 0:
        health = "unhealthy"
    elif warnings > 0:
        health = "needs_attention"
    else:
        health = "healthy"

    return {
        "health": health,
        "ok": ok,
        "warnings": warnings,
        "errors": errors,
        "total_checks": len(checks),
    }

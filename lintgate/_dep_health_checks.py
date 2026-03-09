"""Individual dependency health check functions.

Internal module — import from ``lintgate.dependency_health`` for the public API.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

from lintgate._dep_health_helpers import (
    _CONFLICTING_COMBOS,
    _LOCK_TO_MANIFEST,
    _LOCKFILES,
    HealthCheck,
    _find_venv,
    _format_duration,
    _has_ci_config,
    _has_python_project,
    _load_dep_history,
    _missing_lockfiles,
)

if TYPE_CHECKING:
    from pathlib import Path

# ─── Individual checks ───────────────────────────────────────────────────


def _check_venv(root: Path) -> HealthCheck:
    """Check for a virtual environment in the project."""
    venv_path = _find_venv(root)
    if venv_path:
        # Check if it has a python executable
        python = venv_path / "bin" / "python"
        if python.exists():
            return HealthCheck(
                name="virtual_environment",
                status="ok",
                message=f"Virtual environment found at {venv_path.name}/",
                evidence={"path": str(venv_path), "python": str(python)},
            )
        return HealthCheck(
            name="virtual_environment",
            status="warning",
            message=f"Venv directory {venv_path.name}/ exists but has no python executable",
            suggestion="Recreate with `uv venv .venv`",
            evidence={"path": str(venv_path)},
        )

    if not _has_python_project(root):
        return HealthCheck(
            name="virtual_environment",
            status="ok",
            message="No Python project detected — venv not needed",
        )

    return HealthCheck(
        name="virtual_environment",
        status="error",
        message="No virtual environment found in Python project",
        suggestion="Run `uv venv .venv` then `uv pip install -e '.[dev]'`",
    )


def _check_lockfiles(root: Path) -> list[HealthCheck]:
    """Check that manifests have corresponding lockfiles."""
    checks: list[HealthCheck] = []
    missing = _missing_lockfiles(root)

    if not missing:
        # Check what we DO have
        for _ecosystem, lock_names in _LOCKFILES.items():
            for lock_name in lock_names:
                if (root / lock_name).exists():
                    checks.append(
                        HealthCheck(
                            name=f"lockfile_{lock_name}",
                            status="ok",
                            message=f"Lockfile {lock_name} present",
                        )
                    )
        if not checks:
            # No manifests or lockfiles at all — that's fine for non-package projects
            checks.append(
                HealthCheck(
                    name="lockfile",
                    status="ok",
                    message="No dependency manifests found — lockfile not needed",
                )
            )
        return checks

    for manifest, expected_locks in missing:
        lock_str = " or ".join(expected_locks)
        checks.append(
            HealthCheck(
                name=f"lockfile_for_{manifest}",
                status="error",
                message=f"{manifest} exists but no lockfile ({lock_str}) found",
                suggestion="Run `uv lock` to generate a lockfile",
                evidence={"manifest": manifest, "expected_locks": expected_locks},
            )
        )

    return checks


def _check_lockfile_freshness(root: Path) -> list[HealthCheck]:
    """Check if lockfiles are newer than their manifests."""
    checks: list[HealthCheck] = []

    for lock_name, manifest_name in _LOCK_TO_MANIFEST.items():
        lock_path = root / lock_name
        manifest_path = root / manifest_name
        if not lock_path.exists() or not manifest_path.exists():
            continue

        lock_mtime = lock_path.stat().st_mtime
        manifest_mtime = manifest_path.stat().st_mtime

        if manifest_mtime > lock_mtime:
            delta_s = manifest_mtime - lock_mtime
            delta_desc = _format_duration(delta_s)
            checks.append(
                HealthCheck(
                    name=f"freshness_{lock_name}",
                    status="warning",
                    message=f"{lock_name} is {delta_desc} older than {manifest_name}",
                    suggestion="Run `uv lock` to sync the lockfile",
                    evidence={
                        "lock": lock_name,
                        "manifest": manifest_name,
                        "staleness_seconds": round(delta_s, 1),
                    },
                )
            )
        else:
            checks.append(
                HealthCheck(
                    name=f"freshness_{lock_name}",
                    status="ok",
                    message=f"{lock_name} is up to date with {manifest_name}",
                )
            )

    return checks


def _check_python_version_file(root: Path) -> HealthCheck:
    """Check for .python-version file.

    Escalates to error severity when CI config is present — convergent
    evidence that reproducibility matters for this project.
    """
    pv = root / ".python-version"
    if pv.exists():
        version = pv.read_text().strip()
        return HealthCheck(
            name="python_version_file",
            status="ok",
            message=f".python-version specifies {version}",
            evidence={"version": version},
        )

    if not _has_python_project(root):
        return HealthCheck(
            name="python_version_file",
            status="ok",
            message="No Python project — .python-version not needed",
        )

    # Escalate severity when CI config exists (convergent evidence)
    has_ci = _has_ci_config(root)
    severity = "error" if has_ci else "warning"
    msg = "No .python-version file — Python version not pinned"
    if has_ci:
        msg += " (CI config detected — reproducibility is critical)"

    return HealthCheck(
        name="python_version_file",
        status=severity,
        message=msg,
        suggestion="Create .python-version with your target version (e.g., '3.11')",
        evidence={"has_ci": has_ci},
    )


def _check_conflicting_managers(root: Path) -> list[HealthCheck]:
    """Detect conflicting package manager artifacts.

    Escalates to error when both conflicting files are lockfiles
    (stronger evidence of active conflict vs. leftover artifacts).
    """
    checks: list[HealthCheck] = []

    all_lockfiles = {lf for locks in _LOCKFILES.values() for lf in locks}

    for file_a, file_b, message in _CONFLICTING_COMBOS:
        if (root / file_a).exists() and (root / file_b).exists():
            # Escalate when both are lockfiles (active conflict)
            both_are_locks = file_a in all_lockfiles and file_b in all_lockfiles
            severity = "error" if both_are_locks else "warning"
            checks.append(
                HealthCheck(
                    name=f"conflict_{file_a}_{file_b}",
                    status=severity,
                    message=message,
                    suggestion="Remove one and consolidate on a single package manager (uv recommended)",
                    evidence={
                        "file_a": file_a,
                        "file_b": file_b,
                        "both_lockfiles": both_are_locks,
                    },
                )
            )

    return checks


def _find_unpinned_deps(deps: list) -> list[str]:
    """Extract dependency names that lack version constraints.

    Extracted from ``_check_manifest_health`` to reduce its cyclomatic
    complexity.
    """
    unpinned: list[str] = []
    for dep in deps:
        if not isinstance(dep, str):
            continue
        dep_clean = dep.strip()
        # Strip markers (e.g. "; python_version >= '3.8'")
        if ";" in dep_clean:
            dep_clean = dep_clean.split(";")[0].strip()
        # Check for any version specifier
        if dep_clean and not re.search(r"[><=!~]", dep_clean):
            unpinned.append(dep_clean)
    return unpinned


def _check_manifest_health(root: Path) -> list[HealthCheck]:
    """Check pyproject.toml for required fields and quality signals.

    Professional instinct: A well-maintained manifest declares its
    Python version constraint, build system, and separates dev deps.
    """
    checks: list[HealthCheck] = []
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return checks

    try:
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli as tomllib
        except ModuleNotFoundError:
            return checks

    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        checks.append(
            HealthCheck(
                name="manifest_parse",
                status="error",
                message="pyproject.toml failed to parse",
            )
        )
        return checks

    project = data.get("project", {})

    # Check requires-python
    if not project.get("requires-python"):
        checks.append(
            HealthCheck(
                name="manifest_requires_python",
                status="warning",
                message="pyproject.toml missing requires-python field",
                suggestion="Add requires-python = '>=3.10' (or your minimum version)",
                evidence={
                    "manifest": "pyproject.toml",
                    "issue": "missing_requires_python",
                },
            )
        )
    else:
        checks.append(
            HealthCheck(
                name="manifest_requires_python",
                status="ok",
                message=f"requires-python = '{project['requires-python']}'",
            )
        )

    # Check build-system
    if not data.get("build-system"):
        checks.append(
            HealthCheck(
                name="manifest_build_system",
                status="warning",
                message="pyproject.toml missing [build-system] — cannot install as package",
                suggestion="Add [build-system] with hatchling, setuptools, or flit",
            )
        )

    # Check for unpinned core dependencies
    unpinned = _find_unpinned_deps(project.get("dependencies", []))

    if unpinned:
        checks.append(
            HealthCheck(
                name="manifest_unpinned_deps",
                status="warning",
                message=f"Core dependencies without version constraints: {', '.join(unpinned[:5])}",
                suggestion="Add minimum version constraints (e.g., 'requests>=2.28')",
                evidence={
                    "unpinned": unpinned,
                    "manifest": "pyproject.toml",
                    "issue": "unpinned_core_deps",
                },
            )
        )

    return checks


def _check_dep_churn(project_root: str) -> HealthCheck:
    """Check dependency modification frequency from stored history."""
    history = _load_dep_history(project_root)
    if not history:
        return HealthCheck(
            name="dep_churn",
            status="ok",
            message="No dependency change history recorded",
        )

    events = history.get("events", [])
    now = time.time()
    window_10m = [e for e in events if now - e.get("timestamp", 0) < 600]
    window_1h = [e for e in events if now - e.get("timestamp", 0) < 3600]

    if len(window_10m) >= 5:
        return HealthCheck(
            name="dep_churn",
            status="warning",
            message=f"{len(window_10m)} dependency changes in last 10 minutes",
            suggestion="Stabilize dependencies before continuing feature work",
            evidence={
                "last_10min": len(window_10m),
                "last_1hr": len(window_1h),
                "total_tracked": len(events),
            },
        )

    return HealthCheck(
        name="dep_churn",
        status="ok",
        message=f"{len(window_1h)} dependency changes in last hour",
        evidence={
            "last_10min": len(window_10m),
            "last_1hr": len(window_1h),
            "total_tracked": len(events),
        },
    )

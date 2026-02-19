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

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ─── Constants ────────────────────────────────────────────────────────────

# Lockfiles grouped by ecosystem
_LOCKFILES: dict[str, list[str]] = {
    "python": ["uv.lock", "poetry.lock", "Pipfile.lock", "requirements.txt"],
    "node": ["package-lock.json", "yarn.lock", "pnpm-lock.yaml"],
    "rust": ["Cargo.lock"],
    "go": ["go.sum"],
}

# Manifests that SHOULD have a corresponding lockfile
_MANIFESTS: dict[str, list[str]] = {
    "python": ["pyproject.toml", "setup.cfg", "setup.py", "Pipfile", "requirements.in"],
    "node": ["package.json"],
    "rust": ["Cargo.toml"],
    "go": ["go.mod"],
}

# Lockfile <-> manifest pairing (lockfile -> expected manifest)
_LOCK_TO_MANIFEST: dict[str, str] = {
    "uv.lock": "pyproject.toml",
    "poetry.lock": "pyproject.toml",
    "Pipfile.lock": "Pipfile",
    "package-lock.json": "package.json",
    "yarn.lock": "package.json",
    "pnpm-lock.yaml": "package.json",
    "Cargo.lock": "Cargo.toml",
    "go.sum": "go.mod",
}

# Manifest -> preferred lockfile
_MANIFEST_TO_LOCK: dict[str, list[str]] = {
    "pyproject.toml": ["uv.lock", "poetry.lock"],
    "Pipfile": ["Pipfile.lock"],
    "requirements.in": ["requirements.txt"],
    "package.json": ["package-lock.json", "yarn.lock", "pnpm-lock.yaml"],
    "Cargo.toml": ["Cargo.lock"],
    "go.mod": ["go.sum"],
}

# Venv directory names to detect
_VENV_DIRS = (".venv", "venv", "env", ".env")

# Patterns for global installs (no --target, no venv indicators)
_GLOBAL_INSTALL_PATTERN = re.compile(
    r"^\s*(pip3?\s+install|uv\s+(pip\s+)?install)",
)
_VENV_INDICATOR_FLAGS = ("--target", "-t ", "--prefix")

# Conflicting package manager combos
_CONFLICTING_COMBOS: list[tuple[str, str, str]] = [
    ("Pipfile", "poetry.lock", "Both Pipfile (pipenv) and poetry.lock exist — pick one"),
    ("Pipfile", "uv.lock", "Both Pipfile (pipenv) and uv.lock exist — pick one"),
    ("poetry.lock", "uv.lock", "Both poetry.lock and uv.lock exist — migrate to one"),
]

# State directory for dep churn tracking
DEP_HEALTH_DIR = Path.home() / ".claude" / "lintgate" / "dep_health"


# ─── Data types ───────────────────────────────────────────────────────────


@dataclass
class HealthCheck:
    """A single dependency health check result."""

    name: str
    status: str  # "ok", "warning", "error"
    message: str
    suggestion: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "status": self.status, "message": self.message}
        if self.suggestion:
            d["suggestion"] = self.suggestion
        if self.evidence:
            d["evidence"] = self.evidence
        return d


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
    """Check for .python-version file."""
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

    return HealthCheck(
        name="python_version_file",
        status="warning",
        message="No .python-version file — Python version not pinned",
        suggestion="Create .python-version with your target version (e.g., '3.11')",
    )


def _check_conflicting_managers(root: Path) -> list[HealthCheck]:
    """Detect conflicting package manager artifacts."""
    checks: list[HealthCheck] = []

    for file_a, file_b, message in _CONFLICTING_COMBOS:
        if (root / file_a).exists() and (root / file_b).exists():
            checks.append(
                HealthCheck(
                    name=f"conflict_{file_a}_{file_b}",
                    status="warning",
                    message=message,
                    suggestion="Remove one and consolidate on a single package manager (uv recommended)",
                    evidence={"file_a": file_a, "file_b": file_b},
                )
            )

    return checks


def _check_manifest_health(root: Path) -> list[HealthCheck]:
    """Check pyproject.toml for required fields."""
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


# ─── Helpers ──────────────────────────────────────────────────────────────


def _find_venv(root: Path) -> Path | None:
    """Find a virtual environment directory in the project root."""
    for name in _VENV_DIRS:
        candidate = root / name
        if candidate.is_dir() and (candidate / "pyvenv.cfg").exists():
            return candidate
    return None


def _has_python_project(root: Path) -> bool:
    """Detect if this is a Python project."""
    markers = ("pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "requirements.txt")
    return any((root / m).exists() for m in markers)


def _missing_lockfiles(root: Path) -> list[tuple[str, list[str]]]:
    """Find manifests that lack a corresponding lockfile."""
    missing: list[tuple[str, list[str]]] = []

    for manifest, expected_locks in _MANIFEST_TO_LOCK.items():
        if not (root / manifest).exists():
            continue
        has_lock = any((root / lock).exists() for lock in expected_locks)
        if not has_lock:
            missing.append((manifest, expected_locks))

    return missing


def _stale_lockfiles(root: Path) -> list[tuple[str, str]]:
    """Find lockfiles that are older than their manifest."""
    stale: list[tuple[str, str]] = []

    for lock_name, manifest_name in _LOCK_TO_MANIFEST.items():
        lock_path = root / lock_name
        manifest_path = root / manifest_name
        if not lock_path.exists() or not manifest_path.exists():
            continue
        if manifest_path.stat().st_mtime > lock_path.stat().st_mtime:
            stale.append((manifest_name, lock_name))

    return stale


def _is_global_install(command: str, root: Path) -> bool:
    """Detect if a pip/uv install command is running outside a venv."""
    if not _GLOBAL_INSTALL_PATTERN.search(command):
        return False

    # If the command explicitly targets somewhere, it's not "global"
    if any(flag in command for flag in _VENV_INDICATOR_FLAGS):
        return False

    # If project has a venv, assume the command runs in it
    # (Claude Code typically activates venvs when they exist)
    return not _find_venv(root)


def _record_dep_event(project_root: str, change_kind: str) -> dict[str, Any] | None:
    """Record a dependency change event and return churn info."""
    DEP_HEALTH_DIR.mkdir(parents=True, exist_ok=True)

    import hashlib

    key = hashlib.sha256(project_root.encode()).hexdigest()[:16]
    history_path = DEP_HEALTH_DIR / key

    history = _load_json(history_path) or {"events": []}
    events = history.get("events", [])

    now = time.time()
    events.append({"timestamp": now, "kind": change_kind})

    # Keep last 100 events only
    if len(events) > 100:
        events = events[-100:]

    history["events"] = events
    history["updated_at"] = now
    history["project"] = project_root

    with open(history_path, "w") as f:
        json.dump(history, f)

    # Check churn
    recent = [e for e in events if now - e["timestamp"] < 600]
    return {
        "recent_count": len(recent),
        "is_churning": len(recent) >= 5,
    }


def _load_dep_history(project_root: str) -> dict[str, Any] | None:
    """Load dep change history for a project."""
    import hashlib

    key = hashlib.sha256(project_root.encode()).hexdigest()[:16]
    return _load_json(DEP_HEALTH_DIR / key)


def _load_json(path: Path) -> dict[str, Any] | None:
    """Load a JSON file, returning None on failure."""
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _format_duration(seconds: float) -> str:
    """Human-readable duration string."""
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


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

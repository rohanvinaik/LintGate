"""Dependency health helpers and constants.

Internal module — import from ``lintgate.dependency_health`` for the public API.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
        d: dict[str, Any] = {
            "name": self.name,
            "status": self.status,
            "message": self.message,
        }
        if self.suggestion:
            d["suggestion"] = self.suggestion
        if self.evidence:
            d["evidence"] = self.evidence
        return d


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
    (
        "Pipfile",
        "poetry.lock",
        "Both Pipfile (pipenv) and poetry.lock exist — pick one",
    ),
    ("Pipfile", "uv.lock", "Both Pipfile (pipenv) and uv.lock exist — pick one"),
    ("poetry.lock", "uv.lock", "Both poetry.lock and uv.lock exist — migrate to one"),
]

# State directory for dep churn tracking
DEP_HEALTH_DIR = Path.home() / ".claude" / "lintgate" / "dep_health"


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


def _has_ci_config(root: Path) -> bool:
    """Detect if the project has CI/CD configuration."""
    ci_markers = [
        root / ".github" / "workflows",
        root / ".gitlab-ci.yml",
        root / "Jenkinsfile",
        root / ".circleci",
        root / ".travis.yml",
        root / "azure-pipelines.yml",
        root / ".buildkite",
    ]
    return any(m.exists() for m in ci_markers)


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

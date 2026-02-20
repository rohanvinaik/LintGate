"""Hygiene precheck — command-class behavioral cues.

Classifies planned commands into professional command classes and runs
domain-specific precondition checks. This is the "professional instinct"
layer that says "wait, you're about to pip install globally" or
"you're about to git commit without checking for secrets."

Professional instinct modeled: "Before running a risky command, a senior
engineer checks preconditions."

Each check must:
- Be fast (<50ms)
- Emit machine-verifiable evidence
- Degrade gracefully when tools/state unavailable
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class HygieneWarning:
    """A single hygiene check result."""

    check: str  # e.g. "venv_active"
    message: str  # Human-readable warning
    confidence: float  # 0.0-1.0
    evidence: dict[str, Any] = field(default_factory=dict)
    actionability: str = "advisory"  # "immediate" | "investigate" | "advisory"


@dataclass
class HygieneResult:
    """Result of classifying and checking a planned command."""

    command_class: str | None  # e.g. "pip_install", "git_commit"
    warnings: list[HygieneWarning] = field(default_factory=list)
    recommendation: str = ""


# ── Command class definitions ────────────────────────────────────────────

_COMMAND_CLASSES: dict[str, dict[str, Any]] = {
    "pip_install": {
        "patterns": [
            re.compile(r"pip3?\s+install"),
            re.compile(r"uv\s+(pip\s+)?install"),
            re.compile(r"uv\s+add"),
        ],
        "checks": ["venv_active", "lockfile_exists", "pinned_version"],
    },
    "git_commit": {
        "patterns": [
            re.compile(r"git\s+commit"),
            re.compile(r"git\s+push"),
        ],
        "checks": ["no_staged_secrets", "lockfile_fresh"],
    },
    "env_edit": {
        "patterns": [
            re.compile(r"\.env\b"),
            re.compile(r"export\s+\w+\s*="),
        ],
        "checks": ["gitignore_coverage"],
    },
    "publish_build": {
        "patterns": [
            re.compile(r"python\s+-m\s+build"),
            re.compile(r"twine\s+upload"),
            re.compile(r"uv\s+publish"),
            re.compile(r"npm\s+publish"),
        ],
        "checks": ["clean_working_tree", "lockfile_fresh"],
    },
}

# Venv directory names (shared with dependency_health.py)
_VENV_DIRS = (".venv", "venv", "env")

# Lockfiles to check for
_LOCKFILES = ("uv.lock", "poetry.lock", "Pipfile.lock", "requirements.txt")

# Manifests paired with lockfiles
_MANIFEST_LOCKFILE_PAIRS = {
    "pyproject.toml": ("uv.lock", "poetry.lock"),
    "Pipfile": ("Pipfile.lock",),
    "package.json": ("package-lock.json", "yarn.lock", "pnpm-lock.yaml"),
}


def classify_and_check(planned_action: str, project_root: str) -> HygieneResult:
    """Classify a planned command and run domain-specific checks.

    Args:
        planned_action: Free text describing what the agent plans to do.
        project_root: Absolute path to the project root.

    Returns:
        HygieneResult with command class, warnings, and recommendation.
    """
    command_class = _classify_command(planned_action)
    if command_class is None:
        return HygieneResult(command_class=None, recommendation="No hygiene checks applicable.")

    checks = _COMMAND_CLASSES[command_class]["checks"]
    warnings: list[HygieneWarning] = []

    for check_name in checks:
        check_fn = _CHECK_REGISTRY.get(check_name)
        if check_fn is None:
            continue
        try:
            warning = check_fn(planned_action, project_root)
            if warning is not None:
                warnings.append(warning)
        except Exception:
            # Graceful degradation — never crash the precheck
            pass

    if warnings:
        parts = [w.message for w in warnings[:3]]
        recommendation = " | ".join(parts) + ". Address before proceeding."
    else:
        recommendation = f"Hygiene checks passed for {command_class}."

    return HygieneResult(
        command_class=command_class,
        warnings=warnings,
        recommendation=recommendation,
    )


def _classify_command(planned_action: str) -> str | None:
    """Classify a planned action into a command class."""
    action_lower = planned_action.lower()
    for class_name, class_def in _COMMAND_CLASSES.items():
        for pattern in class_def["patterns"]:
            if pattern.search(action_lower):
                return class_name
    return None


# ── Individual check functions ───────────────────────────────────────────
# Each returns HygieneWarning | None. None means the check passed.


def _check_venv_active(planned_action: str, project_root: str) -> HygieneWarning | None:
    """Check if a virtual environment exists in the project."""
    root = Path(project_root)
    for venv_name in _VENV_DIRS:
        venv_dir = root / venv_name
        if venv_dir.is_dir() and (venv_dir / "bin").is_dir():
            return None  # venv exists

    return HygieneWarning(
        check="venv_active",
        message="No virtual environment found. Installing packages globally is risky.",
        confidence=0.90,
        evidence={"project_root": project_root, "checked_dirs": list(_VENV_DIRS)},
        actionability="immediate",
    )


def _check_lockfile_exists(planned_action: str, project_root: str) -> HygieneWarning | None:
    """Check if any lockfile exists in the project."""
    root = Path(project_root)
    for lockfile in _LOCKFILES:
        if (root / lockfile).exists():
            return None  # lockfile found

    # Only warn if a manifest exists (otherwise the project may not use packages)
    has_manifest = any((root / m).exists() for m in _MANIFEST_LOCKFILE_PAIRS)
    if not has_manifest:
        return None

    return HygieneWarning(
        check="lockfile_exists",
        message="No lockfile found. Dependencies are not reproducible.",
        confidence=0.85,
        evidence={"checked_lockfiles": list(_LOCKFILES)},
        actionability="immediate",
    )


def _check_pinned_version(planned_action: str, project_root: str) -> HygieneWarning | None:
    """Check if pip install commands include version pins."""
    # Extract package names from the command
    # Match patterns like: pip install requests, uv add flask
    install_match = re.search(
        r"(?:pip3?\s+install|uv\s+(?:pip\s+)?install|uv\s+add)\s+(.+)",
        planned_action,
        re.IGNORECASE,
    )
    if not install_match:
        return None

    packages_str = install_match.group(1).strip()
    tokens = packages_str.split()

    # Filter out flags and their arguments
    # Flags that take a value: -r, -c, -e, --requirement, --constraint, --target, -t, etc.
    flags_with_args = {"-r", "-c", "-e", "-t", "--requirement", "--constraint", "--target", "--prefix"}
    packages: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-"):
            if token in flags_with_args:
                skip_next = True  # Next token is the flag's argument
            continue
        packages.append(token)

    if not packages:
        return None

    # Check each package for a version specifier
    unpinned = []
    for pkg in packages:
        # Version specifiers: ==, >=, <=, ~=, !=, <, >
        if not re.search(r"[><=!~]", pkg):
            unpinned.append(pkg)

    if not unpinned:
        return None

    return HygieneWarning(
        check="pinned_version",
        message=f"Installing without version pin: {', '.join(unpinned[:3])}. Pin versions for reproducibility.",
        confidence=0.70,
        evidence={"unpinned_packages": unpinned},
        actionability="advisory",
    )


def _check_no_staged_secrets(planned_action: str, project_root: str) -> HygieneWarning | None:
    """Quick check for secrets in staged diffs before commit."""
    try:
        from lintgate.channels.git_channel import _check_diff_secrets

        findings = _check_diff_secrets(project_root)
        if findings:
            return HygieneWarning(
                check="no_staged_secrets",
                message=f"{len(findings)} potential secret(s) detected in staged diff.",
                confidence=0.85,
                evidence={"secret_count": len(findings)},
                actionability="immediate",
            )
    except Exception:
        pass
    return None


def _check_lockfile_fresh(planned_action: str, project_root: str) -> HygieneWarning | None:
    """Check if lockfile is newer than manifest."""
    root = Path(project_root)

    for manifest_name, lockfile_names in _MANIFEST_LOCKFILE_PAIRS.items():
        manifest = root / manifest_name
        if not manifest.exists():
            continue

        for lockfile_name in lockfile_names:
            lockfile = root / lockfile_name
            if not lockfile.exists():
                continue

            try:
                if manifest.stat().st_mtime > lockfile.stat().st_mtime:
                    return HygieneWarning(
                        check="lockfile_fresh",
                        message=f"{manifest_name} is newer than {lockfile_name}. Lockfile may be stale.",
                        confidence=0.80,
                        evidence={
                            "manifest": manifest_name,
                            "lockfile": lockfile_name,
                        },
                        actionability="investigate",
                    )
            except OSError:
                pass

            return None  # Lockfile exists and is fresh

    return None


def _check_gitignore_coverage(planned_action: str, project_root: str) -> HygieneWarning | None:
    """Check if .env is covered by .gitignore."""
    root = Path(project_root)
    gitignore = root / ".gitignore"

    if not gitignore.exists():
        return HygieneWarning(
            check="gitignore_coverage",
            message="No .gitignore found. Environment files may be committed accidentally.",
            confidence=0.75,
            evidence={"missing": ".gitignore"},
            actionability="advisory",
        )

    try:
        content = gitignore.read_text(errors="replace")
        # Check for .env coverage
        env_patterns = [".env", "*.env", ".env.*", ".env.local"]
        for pattern in env_patterns:
            if pattern in content:
                return None  # .env is covered
    except OSError:
        return None

    return HygieneWarning(
        check="gitignore_coverage",
        message=".env is not in .gitignore. Environment secrets may be committed accidentally.",
        confidence=0.80,
        evidence={"gitignore_exists": True, "env_pattern_missing": True},
        actionability="immediate",
    )


def _check_clean_working_tree(planned_action: str, project_root: str) -> HygieneWarning | None:
    """Check if the working tree is clean (for publish/build commands)."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=project_root,
        )
        if result.returncode != 0:
            return None  # Not a git repo or git error

        dirty_files = [line for line in result.stdout.splitlines() if line.strip()]
        if dirty_files:
            return HygieneWarning(
                check="clean_working_tree",
                message=f"Working tree has {len(dirty_files)} uncommitted change(s). Commit or stash before publishing.",
                confidence=0.85,
                evidence={"dirty_count": len(dirty_files)},
                actionability="immediate",
            )
    except (subprocess.TimeoutExpired, OSError):
        pass

    return None


# ── Check registry ───────────────────────────────────────────────────────

_CHECK_REGISTRY: dict[str, Any] = {
    "venv_active": _check_venv_active,
    "lockfile_exists": _check_lockfile_exists,
    "pinned_version": _check_pinned_version,
    "no_staged_secrets": _check_no_staged_secrets,
    "lockfile_fresh": _check_lockfile_fresh,
    "gitignore_coverage": _check_gitignore_coverage,
    "clean_working_tree": _check_clean_working_tree,
}

"""Quality infrastructure audit — checks completeness of CI/badge/config artifacts.

Shared module used by:
- hygiene.py (pre-commit/pre-push agent check)
- git_channel.py (ControlPlane Check 5)
- onboarding_tools.py (getting_started quality check)
- Pre-push hook and CI gate (via CLI entry point)

Each project managed by LintGate should have a complete set of quality
infrastructure artifacts. This module audits that completeness and provides
a CLI entry point for hard enforcement in hooks and CI.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── Required artifacts ───────────────────────────────────────────────────

# Maps artifact name to relative path from project root.
_REQUIRED_ARTIFACTS: dict[str, str] = {
    "codeclimate": ".codeclimate.yml",
    "sonar_properties": "sonar-project.properties",
    "coveragerc": ".coveragerc",
    "gitleaks": ".gitleaks.toml",
    "workflow_sonarcloud": os.path.join(".github", "workflows", "sonarcloud.yml"),
    "workflow_tests": os.path.join(".github", "workflows", "tests.yml"),
    "workflow_qlty": os.path.join(".github", "workflows", "qlty.yml"),
    "workflow_security": os.path.join(".github", "workflows", "security-lite.yml"),
    "workflow_scorecard": os.path.join(".github", "workflows", "scorecard.yml"),
    "workflow_codeql": os.path.join(".github", "workflows", "codeql.yml"),
    "workflow_quality_gate": os.path.join(".github", "workflows", "quality-infra-gate.yml"),
    "pre_push_hook": os.path.join(".githooks", "pre-push"),
    "qlty_toml": os.path.join(".qlty", "qlty.toml"),
    "dependabot": os.path.join(".github", "dependabot.yml"),
    "security_md": "SECURITY.md",
}

# Badge fingerprints that must appear in the README managed block.
_REQUIRED_BADGE_FINGERPRINTS: tuple[str, ...] = (
    "actions/workflows/tests.yml/badge.svg",
    "actions/workflows/security-lite.yml/badge.svg",
    "metric=alert_status",
    "metric=coverage",
    "metric=security_rating",
    "metric=sqale_rating",
    "metric=reliability_rating",
    "securityscorecards.dev",
)

# Managed badge block markers (must match onboarding_tools.py).
_BADGE_BLOCK_START = "<!-- lintgate:quality-badges:start -->"
_BADGE_BLOCK_END = "<!-- lintgate:quality-badges:end -->"

_README_NAMES = ("README.md", "readme.md", "Readme.md", "README.MD")

_GITHUB_REMOTE_RE = re.compile(r"github\.com[:/]([^/]+)/([^/.\s]+)")


# ── Result dataclass ─────────────────────────────────────────────────────


@dataclass
class QualityAuditResult:
    """Result of auditing quality infrastructure completeness."""

    complete: bool
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    has_github_remote: bool = False
    badge_count: int = 0
    expected_badge_count: int = len(_REQUIRED_BADGE_FINGERPRINTS)
    badge_fingerprints_ok: bool = False


# ── Core audit function ──────────────────────────────────────────────────


def audit_quality_infrastructure(project_root: str) -> QualityAuditResult:
    """Audit quality infrastructure completeness for a project.

    Args:
        project_root: Absolute path to the project root.

    Returns:
        QualityAuditResult with completeness status and artifact details.
        For non-git or non-GitHub projects, returns complete=True to avoid
        false blocks.
    """
    root = Path(project_root)

    # Not a git repo → no quality infra expected
    if not _is_git_repo(project_root):
        return QualityAuditResult(complete=True, has_github_remote=False)

    # No GitHub remote → quality infra not applicable
    has_github = _has_github_remote(project_root)
    if not has_github:
        return QualityAuditResult(complete=True, has_github_remote=False)

    # Check each artifact
    present: list[str] = []
    missing: list[str] = []
    for name, rel_path in _REQUIRED_ARTIFACTS.items():
        if (root / rel_path).exists():
            present.append(name)
        else:
            missing.append(name)

    # Check badge fingerprints in README
    badge_count, badge_ok = _check_badge_fingerprints(project_root)

    complete = len(missing) == 0 and badge_ok

    return QualityAuditResult(
        complete=complete,
        present=present,
        missing=missing,
        has_github_remote=True,
        badge_count=badge_count,
        expected_badge_count=len(_REQUIRED_BADGE_FINGERPRINTS),
        badge_fingerprints_ok=badge_ok,
    )


# ── Helper functions ─────────────────────────────────────────────────────


def _is_git_repo(project_root: str) -> bool:
    """Check if the directory is inside a git repository."""
    if (Path(project_root) / ".git").exists():
        return True
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=project_root,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _has_github_remote(project_root: str) -> bool:
    """Check if the git repo has a GitHub remote."""
    try:
        result = subprocess.run(
            ["git", "remote", "-v"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=project_root,
        )
        if result.returncode != 0 or not result.stdout:
            return False
        return bool(_GITHUB_REMOTE_RE.search(result.stdout))
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _check_badge_fingerprints(project_root: str) -> tuple[int, bool]:
    """Check README for badge managed block with required fingerprints.

    Returns:
        (badge_count, all_fingerprints_present)
    """
    root = Path(project_root)
    readme_path = None
    for name in _README_NAMES:
        candidate = root / name
        if candidate.exists():
            readme_path = candidate
            break

    if readme_path is None:
        return 0, False

    try:
        content = readme_path.read_text(errors="ignore")
    except OSError:
        return 0, False

    # Check for managed block
    if _BADGE_BLOCK_START in content and _BADGE_BLOCK_END in content:
        start = content.find(_BADGE_BLOCK_START)
        end = content.find(_BADGE_BLOCK_END, start)
        if end == -1:
            return 0, False
        managed_block = content[start : end + len(_BADGE_BLOCK_END)]
        found = sum(1 for fp in _REQUIRED_BADGE_FINGERPRINTS if fp in managed_block)
        return found, found == len(_REQUIRED_BADGE_FINGERPRINTS)

    # Fallback: check content directly
    found = sum(1 for fp in _REQUIRED_BADGE_FINGERPRINTS if fp in content)
    return found, found == len(_REQUIRED_BADGE_FINGERPRINTS)


# ── CLI entry point ──────────────────────────────────────────────────────


def _cli_main() -> int:
    """CLI entry point for pre-push hook and CI gate.

    Usage: python -m lintgate.quality_infra --enforce /path/to/project
    Returns 0 if complete, 1 if missing artifacts.
    """
    args = sys.argv[1:]

    enforce = "--enforce" in args
    project_root = None
    for arg in args:
        if arg != "--enforce":
            project_root = arg
            break

    if project_root is None:
        project_root = os.getcwd()

    result = audit_quality_infrastructure(project_root)

    if not result.has_github_remote:
        print("[quality-infra] No GitHub remote detected; skipping audit.")
        return 0

    if result.complete:
        print(
            f"[quality-infra] Complete: {len(result.present)} artifacts present, "
            f"{result.badge_count}/{result.expected_badge_count} badge fingerprints OK."
        )
        return 0

    # Report missing items
    print(f"[quality-infra] INCOMPLETE: {len(result.missing)} artifact(s) missing:")
    for name in result.missing:
        rel_path = _REQUIRED_ARTIFACTS.get(name, name)
        print(f"  - {name}: {rel_path}")

    if not result.badge_fingerprints_ok:
        print(
            f"  - badges: {result.badge_count}/{result.expected_badge_count} "
            "fingerprints found in README"
        )

    print()
    print("Fix: run setup_github_quality(path=..., write=True)")

    return 1 if enforce else 0


if __name__ == "__main__":
    sys.exit(_cli_main())

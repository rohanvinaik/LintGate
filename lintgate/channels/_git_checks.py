"""Individual git hygiene check functions.

Extracted from git_channel.py to keep the main channel file under 400 lines.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from lintgate.controlplane.types import RepairAction
from lintgate.types import LintIssue

from ._git_helpers import _parse_diff_stat_totals


def _check_working_tree_scope(project_root: str) -> list[LintIssue]:
    """Advisory when working tree has many uncommitted changes (>10 files).

    Counts both modified/staged files and untracked files separately.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=project_root,
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, OSError):
        return []

    modified_count = 0
    untracked_count = 0
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        if line.startswith("??"):
            untracked_count += 1
        else:
            modified_count += 1

    total = modified_count + untracked_count
    if total <= 10:
        return []

    return [
        LintIssue(
            linter="git_channel",
            kind="wide_working_tree",
            message=(
                f"Working tree has {total} uncommitted files "
                f"({modified_count} modified, {untracked_count} untracked). "
                "Consider committing or stashing unrelated changes "
                "to narrow the analysis scope."
            ),
            severity="informational",
            evidence={
                "modified_count": modified_count,
                "untracked_count": untracked_count,
                "total": total,
            },
        )
    ]


def _check_large_changes(project_root: str) -> list[LintIssue]:
    """Check for large uncommitted changes (>500 lines)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", "--cached"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=project_root,
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, OSError):
        return []

    insertions, deletions = _parse_diff_stat_totals(result.stdout)
    total = insertions + deletions
    if total <= 500:
        return []

    return [
        LintIssue(
            linter="git_channel",
            kind="large_staged_changes",
            message=(
                f"Large staged changes: {insertions} insertions, "
                f"{deletions} deletions ({total} total). "
                "Consider committing in smaller chunks."
            ),
            severity="informational",
        )
    ]


def _check_lockfile_freshness(
    project_root: str,
) -> tuple[list[LintIssue], list[RepairAction]]:
    """Check if lockfile is older than manifest (pyproject.toml)."""
    findings: list[LintIssue] = []
    repairs: list[RepairAction] = []

    root = Path(project_root)
    manifest = root / "pyproject.toml"
    lockfile = root / "uv.lock"

    if not manifest.exists():
        return findings, repairs

    if not lockfile.exists():
        # Check for other lockfile types
        alt_lockfiles = [
            root / "requirements.txt",
            root / "poetry.lock",
            root / "Pipfile.lock",
        ]
        if not any(lf.exists() for lf in alt_lockfiles):
            findings.append(
                LintIssue(
                    linter="git_channel",
                    kind="missing_lockfile",
                    message="No lockfile found (uv.lock, requirements.txt, poetry.lock). Dependencies are not reproducible.",
                    severity="informational",
                )
            )
            repairs.append(
                RepairAction(
                    channel="git",
                    kind="command",
                    summary="Create lockfile: uv lock",
                    payload={"command": "uv lock", "cwd": project_root},
                    safe=True,
                )
            )
        return findings, repairs

    # Check if manifest is newer than lockfile
    try:
        manifest_mtime = manifest.stat().st_mtime
        lockfile_mtime = lockfile.stat().st_mtime
        if manifest_mtime > lockfile_mtime:
            findings.append(
                LintIssue(
                    linter="git_channel",
                    kind="stale_lockfile",
                    message="pyproject.toml is newer than uv.lock. Lockfile may be out of date.",
                    severity="informational",
                )
            )
            repairs.append(
                RepairAction(
                    channel="git",
                    kind="command",
                    summary="Refresh lockfile: uv lock",
                    payload={"command": "uv lock", "cwd": project_root},
                    safe=True,
                )
            )
    except OSError:
        pass

    return findings, repairs


def _check_sensitive_files(project_root: str) -> list[LintIssue]:
    """Check for untracked sensitive files that might be accidentally committed."""
    findings: list[LintIssue] = []

    sensitive_patterns = {
        ".env",
        ".env.local",
        "credentials.json",
        "secrets.yaml",
        ".aws/credentials",
        "id_rsa",
        "id_ed25519",
    }

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=project_root,
        )
        if result.returncode != 0:
            return findings

        for line in result.stdout.splitlines():
            status = line[:2].strip()
            filename = line[3:].strip().strip('"')

            # Check if this is a sensitive file that's being tracked/staged
            basename = os.path.basename(filename)
            if basename in sensitive_patterns and status in ("A", "??", "M"):
                findings.append(
                    LintIssue(
                        linter="git_channel",
                        kind="sensitive_file",
                        message=f"Sensitive file detected: {filename}. Ensure it's in .gitignore.",
                        file=os.path.join(project_root, filename),
                        severity="warning",
                    )
                )

    except (subprocess.TimeoutExpired, OSError):
        pass

    return findings


def _check_quality_infrastructure(
    project_root: str,
) -> tuple[list[LintIssue], list[RepairAction]]:
    """Check quality infrastructure completeness (Check 5).

    Warning-level severity: quality infrastructure gaps mean the CI
    quality-infra-gate would fail on push.
    """
    findings: list[LintIssue] = []
    repairs: list[RepairAction] = []

    try:
        from lintgate.quality_infra import audit_quality_infrastructure

        result = audit_quality_infrastructure(project_root)
    except Exception:
        return findings, repairs  # Graceful degradation

    if not result.has_github_remote:
        return findings, repairs  # Not a GitHub project

    if result.complete:
        return findings, repairs  # All good

    missing = result.missing
    findings.append(
        LintIssue(
            linter="git_channel",
            kind="missing_quality_infra",
            message=(
                f"Quality infrastructure incomplete: {len(missing)} artifact(s) missing. "
                "CI quality-infra-gate will fail. "
                "Run setup_github_quality(write=True)."
            ),
            severity="warning",
            evidence={
                "missing": missing[:5],
                "present_count": len(result.present),
                "badge_fingerprints_ok": result.badge_fingerprints_ok,
            },
            suggestions=[
                'Run setup_github_quality(path="...", write=True) to deploy missing artifacts',
                "Missing: " + ", ".join(missing[:5]),
            ],
        )
    )

    repairs.append(
        RepairAction(
            channel="git",
            kind="command",
            summary="Deploy missing quality infrastructure",
            payload={
                "tool": "setup_github_quality",
                "args": {"path": project_root, "write": True},
            },
            safe=True,
        )
    )

    return findings, repairs

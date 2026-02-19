"""Git channel — supervision for git hygiene.

Checks:
1. Large uncommitted changes (>500 lines staged)
2. Lockfile-manifest mismatch (pyproject.toml newer than uv.lock)
3. Untracked sensitive files (.env, credentials)
4. Branch naming policy (if configured)

Advisory only — git hygiene issues are informational suggestions.
All checks use subprocess git commands with timeout protection.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    RepairAction,
    SupervisionEvent,
)
from lintgate.types import LintIssue


class GitChannel:
    """Supervision channel for git hygiene.

    Advisory only — git issues are informational suggestions.
    """

    name = "git"
    timeout_ms = 3000
    blocking_capable = False  # Advisory

    def should_run(self, event: SupervisionEvent, config: ControlPlaneConfig) -> bool:
        """Run on any change that modifies files (i.e., most events)."""
        if event.surface == "mcp":
            return True
        classification = event.change_classification
        if classification is None:
            return False
        # Skip read-only operations
        return classification.risk_level != "none"

    def execute(self, event: SupervisionEvent, config: ControlPlaneConfig) -> ChannelResult:
        """Execute git hygiene checks."""
        start = time.perf_counter()
        findings: list[LintIssue] = []
        repairs: list[RepairAction] = []

        project_root = event.project_root

        # Only run if this is a git repository
        if not _is_git_repo(project_root):
            return ChannelResult(
                channel=self.name,
                status="skip",
                severity="none",
                metrics={"reason": "not_a_git_repo"},
            )

        # Check 1: Large uncommitted changes
        large_change_findings = _check_large_changes(project_root)
        findings.extend(large_change_findings)

        # Check 2: Lockfile-manifest mismatch
        lockfile_findings, lockfile_repairs = _check_lockfile_freshness(project_root)
        findings.extend(lockfile_findings)
        repairs.extend(lockfile_repairs)

        # Check 3: Sensitive files
        sensitive_findings = _check_sensitive_files(project_root)
        findings.extend(sensitive_findings)

        elapsed_ms = (time.perf_counter() - start) * 1000
        status = "fail" if findings else "pass"
        severity = "informational" if findings else "none"

        return ChannelResult(
            channel=self.name,
            status=status,
            severity=severity,
            findings=findings,
            repairs=repairs,
            metrics={
                "checks_run": 3,
                "issue_count": len(findings),
            },
            duration_ms=elapsed_ms,
        )


# ── Git checks ───────────────────────────────────────────────────────────


def _is_git_repo(project_root: str) -> bool:
    """Check if the directory is inside a git repository."""
    git_dir = Path(project_root) / ".git"
    if git_dir.exists():
        return True
    # Check parent directories
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=project_root,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _check_large_changes(project_root: str) -> list[LintIssue]:
    """Check for large uncommitted changes (>500 lines)."""
    findings: list[LintIssue] = []

    try:
        result = subprocess.run(
            ["git", "diff", "--stat", "--cached"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=project_root,
        )
        if result.returncode != 0:
            return findings

        # Parse the summary line: " N files changed, X insertions(+), Y deletions(-)"
        for line in result.stdout.splitlines():
            if "insertions" in line or "deletions" in line:
                import re

                insertions = 0
                deletions = 0
                ins_match = re.search(r"(\d+) insertion", line)
                if ins_match:
                    insertions = int(ins_match.group(1))
                del_match = re.search(r"(\d+) deletion", line)
                if del_match:
                    deletions = int(del_match.group(1))

                total = insertions + deletions
                if total > 500:
                    findings.append(
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
                    )

    except (subprocess.TimeoutExpired, OSError):
        pass

    return findings


def _check_lockfile_freshness(project_root: str) -> tuple[list[LintIssue], list[RepairAction]]:
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
        alt_lockfiles = [root / "requirements.txt", root / "poetry.lock", root / "Pipfile.lock"]
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

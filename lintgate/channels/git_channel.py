"""Git channel — supervision for git hygiene.

Checks:
1. Large uncommitted changes (>500 lines staged)
2. Lockfile-manifest mismatch (pyproject.toml newer than uv.lock)
3. Untracked sensitive files (.env, credentials)
4. Secrets in staged diffs (API keys, tokens, private keys, connection strings)
5. Quality infrastructure completeness (CI, badges, configs)

Advisory for checks 1-4. Check 5 escalates to warning severity when
quality infrastructure is incomplete (CI quality-infra-gate would fail).
All checks use subprocess git commands with timeout protection.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Literal

from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    RepairAction,
    SupervisionEvent,
)
from lintgate.types import LintIssue

# Dependency manifest/lockfile basenames — when these are in files_changed,
# the lockfile freshness check should still run even on hooks.
_DEPENDENCY_FILES = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-test.txt",
        "uv.lock",
        "poetry.lock",
        "Pipfile",
        "Pipfile.lock",
    }
)


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

        is_hook = event.surface == "hook"

        # Check 1: Large uncommitted changes — skip on hooks (always true during dev)
        if not is_hook:
            findings.extend(_check_large_changes(project_root))

        # Check 2: Lockfile-manifest mismatch — skip on hooks unless dep files changed
        run_lockfile_check = not is_hook or bool(
            {os.path.basename(f) for f in event.files_changed} & _DEPENDENCY_FILES
        )
        if run_lockfile_check:
            lockfile_findings, lockfile_repairs = _check_lockfile_freshness(project_root)
            findings.extend(lockfile_findings)
            repairs.extend(lockfile_repairs)

        # Check 3: Sensitive files — always run (security-relevant)
        findings.extend(_check_sensitive_files(project_root))

        # Check 4: Secrets in staged diffs — always run (security-relevant)
        secrets_count = len(findings)  # snapshot before secrets check
        findings.extend(_check_diff_secrets(project_root))
        secrets_count = len(findings) - secrets_count  # delta = secrets found

        # Check 5: Quality infrastructure completeness — skip on hooks
        qi_findings: list[LintIssue] = []
        qi_repairs: list[RepairAction] = []
        if not is_hook:
            qi_findings, qi_repairs = _check_quality_infrastructure(project_root)
            findings.extend(qi_findings)
            repairs.extend(qi_repairs)

        elapsed_ms = (time.perf_counter() - start) * 1000
        status: Literal["pass", "fail"] = "fail" if findings else "pass"
        # Escalate severity if secrets or quality infra issues found (warning-level)
        severity: Literal["blocking", "warning", "informational", "none"] = "none"
        if findings:
            severity = "informational"
            if any(f.severity == "warning" for f in findings):
                severity = "warning"

        return ChannelResult(
            channel=self.name,
            status=status,
            severity=severity,
            findings=findings,
            repairs=repairs,
            metrics={
                "checks_run": (
                    2
                    + (0 if is_hook else 1)
                    + (1 if run_lockfile_check else 0)
                    + (0 if is_hook else 1)
                ),
                "issue_count": len(findings),
                "secrets_found": secrets_count,
                "quality_infra_findings": len(qi_findings),
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


def _parse_diff_stat_totals(stat_output: str) -> tuple[int, int]:
    """Parse git diff --stat output for total insertions and deletions.

    Returns (insertions, deletions). Returns (0, 0) if no summary line found.
    """
    for line in stat_output.splitlines():
        if "insertions" not in line and "deletions" not in line:
            continue
        ins_match = re.search(r"(\d+) insertion", line)
        del_match = re.search(r"(\d+) deletion", line)
        return (
            int(ins_match.group(1)) if ins_match else 0,
            int(del_match.group(1)) if del_match else 0,
        )
    return 0, 0


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


# ── Secrets detection patterns ──────────────────────────────────────────

# Each pattern: (name, compiled regex, confidence)
# Higher confidence = fewer false positives expected
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str], float]] = [
    (
        "aws_access_key",
        re.compile(r"AKIA[0-9A-Z]{16}"),
        0.95,
    ),
    (
        "private_key",
        re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
        0.99,
    ),
    (
        "github_token",
        re.compile(r"(ghp_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9_]{20,}"),
        0.95,
    ),
    (
        "gitlab_token",
        re.compile(r"glpat-[A-Za-z0-9\-_]{20,}"),
        0.95,
    ),
    (
        "connection_string",
        re.compile(r"(?i)(postgres|mysql|mongodb|redis)://[^\s]+:[^\s]+@"),
        0.90,
    ),
    (
        "generic_api_key",
        re.compile(r"(?i)(api[_-]?key|secret[_-]?key)\s*[:=]\s*['\"][A-Za-z0-9+/=_\-]{20,}['\"]"),
        0.80,
    ),
    (
        "generic_secret",
        re.compile(
            r"(?i)(token|secret|password|passwd|credential)\s*[:=]\s*['\"][^\s'\"]{16,}['\"]"
        ),
        0.75,
    ),
]


def _match_secret_pattern(
    added_content: str,
    current_file: str | None,
    approx_line: int | None,
    project_root: str,
) -> LintIssue | None:
    """Match a single addition line against secret patterns.

    Returns the first matching LintIssue, or None.
    """
    for pattern_name, pattern_re, confidence in _SECRET_PATTERNS:
        if not pattern_re.search(added_content):
            continue
        file_path = os.path.join(project_root, current_file) if current_file else None
        return LintIssue(
            linter="git_channel",
            kind="secret_in_diff",
            message=(
                f"Potential secret detected in staged diff ({pattern_name}). "
                f"Review before committing."
            ),
            file=file_path,
            line=approx_line,
            severity="warning",
            confidence=confidence,
            evidence={"pattern": pattern_name, "file": current_file},
            suggestions=[
                "Remove the secret from the file",
                "Use environment variables instead",
                "Add to .gitignore if it's a secrets file",
            ],
        )
    return None


def _iter_diff_additions(
    diff_output: str,
) -> list[tuple[str | None, str, int | None]]:
    """Parse unified diff into addition lines with file context.

    Returns list of (file_path, added_content, approx_line_number) tuples.
    Only yields addition lines (+), skipping removals and context.
    """
    additions: list[tuple[str | None, str, int | None]] = []
    current_file: str | None = None
    hunk_start: int | None = None
    line_offset = 0

    for line in diff_output.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            hunk_start = None
            line_offset = 0
        elif line.startswith("+++ "):
            current_file = None
        elif line.startswith("@@"):
            hunk_match = re.search(r"\+(\d+)", line)
            if hunk_match:
                hunk_start = int(hunk_match.group(1))
                line_offset = 0
        elif line.startswith("+") and not line.startswith("+++"):
            line_offset += 1
            approx = (hunk_start or 0) + line_offset - 1
            additions.append(
                (
                    current_file,
                    line[1:],  # strip leading '+'
                    approx if hunk_start else None,
                )
            )

    return additions


def _check_diff_secrets(project_root: str) -> list[LintIssue]:
    """Scan staged diff content for embedded secrets.

    Professional instinct: Never commit secrets. A senior engineer reviews
    diffs for credentials before every commit.

    Only scans addition lines (+) to avoid flagging removals or context.
    Never includes actual secret values in issue messages.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--unified=0"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=project_root,
        )
        if result.returncode != 0 or not result.stdout:
            return []
    except (subprocess.TimeoutExpired, OSError):
        return []

    findings: list[LintIssue] = []
    for file_path, added_content, approx_line in _iter_diff_additions(result.stdout):
        finding = _match_secret_pattern(added_content, file_path, approx_line, project_root)
        if finding:
            findings.append(finding)

    return findings


# ── Quality infrastructure check ─────────────────────────────────────────


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

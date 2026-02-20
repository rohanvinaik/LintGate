"""Git channel — supervision for git hygiene.

Checks:
1. Large uncommitted changes (>500 lines staged)
2. Lockfile-manifest mismatch (pyproject.toml newer than uv.lock)
3. Untracked sensitive files (.env, credentials)
4. Secrets in staged diffs (API keys, tokens, private keys, connection strings)

Advisory only — git hygiene issues are informational suggestions.
All checks use subprocess git commands with timeout protection.
"""

from __future__ import annotations

import os
import re
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

        # Check 4: Secrets in staged diffs
        secrets_findings = _check_diff_secrets(project_root)
        findings.extend(secrets_findings)

        elapsed_ms = (time.perf_counter() - start) * 1000
        status = "fail" if findings else "pass"
        # Escalate severity if secrets found (warning-level)
        severity = "none"
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
                "checks_run": 4,
                "issue_count": len(findings),
                "secrets_found": len(secrets_findings),
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
        re.compile(
            r"(?i)(api[_-]?key|apikey|secret[_-]?key)\s*[:=]\s*['\"][A-Za-z0-9+/=_\-]{20,}['\"]"
        ),
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


def _check_diff_secrets(project_root: str) -> list[LintIssue]:
    """Scan staged diff content for embedded secrets.

    Professional instinct: Never commit secrets. A senior engineer reviews
    diffs for credentials before every commit.

    Only scans addition lines (+) to avoid flagging removals or context.
    Never includes actual secret values in issue messages.
    """
    findings: list[LintIssue] = []

    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--unified=0"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=project_root,
        )
        if result.returncode != 0 or not result.stdout:
            return findings
    except (subprocess.TimeoutExpired, OSError):
        return findings

    current_file: str | None = None
    current_hunk_start: int | None = None
    line_offset = 0

    for line in result.stdout.splitlines():
        # Track which file we're in
        if line.startswith("+++ b/"):
            current_file = line[6:]
            current_hunk_start = None
            line_offset = 0
            continue

        if line.startswith("+++ "):
            # Handle non-standard diff headers
            current_file = None
            continue

        # Track hunk headers for line numbers: @@ -old,count +new,count @@
        if line.startswith("@@"):
            hunk_match = re.search(r"\+(\d+)", line)
            if hunk_match:
                current_hunk_start = int(hunk_match.group(1))
                line_offset = 0
            continue

        # Only scan addition lines (new content being staged)
        if not line.startswith("+") or line.startswith("+++"):
            continue

        added_content = line[1:]  # Strip the leading '+'
        line_offset += 1
        approx_line = (current_hunk_start or 0) + line_offset - 1

        for pattern_name, pattern_re, confidence in _SECRET_PATTERNS:
            if pattern_re.search(added_content):
                file_path = os.path.join(project_root, current_file) if current_file else None
                findings.append(
                    LintIssue(
                        linter="git_channel",
                        kind="secret_in_diff",
                        message=(
                            f"Potential secret detected in staged diff ({pattern_name}). "
                            f"Review before committing."
                        ),
                        file=file_path,
                        line=approx_line if current_hunk_start else None,
                        severity="warning",
                        confidence=confidence,
                        evidence={
                            "pattern": pattern_name,
                            "file": current_file,
                        },
                        suggestions=[
                            "Remove the secret from the file",
                            "Use environment variables instead",
                            "Add to .gitignore if it's a secrets file",
                        ],
                    )
                )
                break  # One finding per line (avoid duplicate alerts)

    return findings

"""Secrets detection patterns and staged diff scanning.

Extracted from git_channel.py to keep the main channel file under 400 lines.
"""

from __future__ import annotations

import os
import re
import subprocess

from lintgate.types import LintIssue

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

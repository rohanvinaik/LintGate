"""Shared subprocess wrapper for git and CLI commands.

Centralizes the repeated pattern of:
    subprocess.run(cmd, capture_output=True, text=True, timeout=N, cwd=...)
    + check returncode
    + handle TimeoutExpired/OSError

Extracted from ~57 call sites across 30 files (STRUCT006 finding).
"""

from __future__ import annotations

import subprocess


def run_cmd(
    cmd: list[str],
    *,
    cwd: str | None = None,
    timeout: int = 5,
) -> subprocess.CompletedProcess[str] | None:
    """Run a command, returning CompletedProcess on success or None on failure/timeout.

    Success means returncode == 0.  Failures (non-zero exit), timeouts, and
    OS errors all map to None so callers can use a simple ``if result:`` guard.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        if result.returncode == 0:
            return result
        return None
    except (subprocess.TimeoutExpired, OSError):
        return None

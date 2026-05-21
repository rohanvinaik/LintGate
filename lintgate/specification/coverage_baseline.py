"""Coverage-baseline builder — populate .coverage with per-test contexts.

The dynamic-linkage layer (Layer 1 of mutation discovery) reads the
``.coverage`` SQLite database for per-test context data, which pytest
only produces when run with ``--cov-context=test`` (or the underlying
``coverage run --dynamic-context=test_function``). Without that data,
Layer 1 always returns empty and every mutation run falls back to
name-based heuristics.

This module builds the baseline on demand. Call ``ensure_coverage_baseline``
once before a mutation sweep (or let a controlplane preflight do it) and
the dynamic-linkage layer will have ground-truth data for every function
covered by the test suite — making discovery robust across every naming
convention.

Not invoked automatically from ``load_test_callables`` because pytest
can take minutes on a large suite. Opt-in by design.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass


@dataclass
class BaselineResult:
    status: str  # "already_present", "built", "skipped", "failed"
    coverage_db: str
    duration_s: float = 0.0
    message: str = ""


def _has_contexts(coverage_db: str) -> bool:
    """Return True when .coverage already has dynamic-context rows."""
    if not os.path.isfile(coverage_db):
        return False
    try:
        import sqlite3

        with sqlite3.connect(coverage_db) as conn:
            cur = conn.execute("SELECT COUNT(*) FROM context WHERE context != ''")
            return cur.fetchone()[0] > 0
    except Exception:
        return False


def ensure_coverage_baseline(
    project_root: str,
    *,
    force: bool = False,
    pytest_args: list[str] | None = None,
    timeout_s: int = 600,
    python_bin: str | None = None,
) -> BaselineResult:
    """Ensure ``.coverage`` in *project_root* contains per-test contexts.

    When ``.coverage`` already has context rows and ``force`` is False,
    returns immediately with status="already_present". Otherwise runs::

        <python> -m pytest <pytest_args> --cov=. --cov-context=test -q

    with the working directory set to *project_root*. Pytest exit codes
    other than 0 (all passed) and 1 (some failed) are treated as probe
    failure — we still want the coverage data from a red suite. Timeouts
    are caught and reported as failures without raising.
    """
    coverage_db = os.path.join(project_root, ".coverage")

    if not force and _has_contexts(coverage_db):
        return BaselineResult(
            status="already_present",
            coverage_db=coverage_db,
            message="existing .coverage has per-test contexts",
        )

    bin_path = python_bin or shutil.which("python") or ""
    if not bin_path:
        return BaselineResult(
            status="failed",
            coverage_db=coverage_db,
            message="no python interpreter on PATH",
        )

    cmd = [bin_path, "-m", "pytest"]
    if pytest_args:
        cmd.extend(pytest_args)
    cmd.extend(["--cov=.", "--cov-context=test", "-q", "--no-header"])

    start = time.time()
    try:
        completed = subprocess.run(  # noqa: S603  # pytest invocation, controlled args
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return BaselineResult(
            status="failed",
            coverage_db=coverage_db,
            duration_s=time.time() - start,
            message=f"pytest timed out after {timeout_s}s",
        )
    except OSError as exc:
        return BaselineResult(
            status="failed",
            coverage_db=coverage_db,
            duration_s=time.time() - start,
            message=f"pytest launch failed: {exc}",
        )

    duration = time.time() - start

    # Exit 0 = all passed, 1 = tests ran with failures. Both produce usable
    # coverage data. Exit 5 = no tests collected — skipped, not failed.
    if completed.returncode == 5:
        return BaselineResult(
            status="skipped",
            coverage_db=coverage_db,
            duration_s=duration,
            message="pytest collected no tests",
        )
    if completed.returncode not in (0, 1):
        tail = (completed.stderr or completed.stdout or "")[-200:]
        return BaselineResult(
            status="failed",
            coverage_db=coverage_db,
            duration_s=duration,
            message=f"pytest exit {completed.returncode}: {tail}",
        )

    if not _has_contexts(coverage_db):
        return BaselineResult(
            status="failed",
            coverage_db=coverage_db,
            duration_s=duration,
            message="pytest ran but .coverage has no context rows "
            "(is pytest-cov installed?)",
        )

    return BaselineResult(
        status="built",
        coverage_db=coverage_db,
        duration_s=duration,
        message="baseline built with per-test contexts",
    )

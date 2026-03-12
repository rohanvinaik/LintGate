"""Phase 1B: Auto-fix safe lint issues.

Wraps ruff check --fix (safe rules), ruff format, and import sorting.
Always dry_run by default — the agent must explicitly approve changes.

Design: thin wrapper over ruff, not a reimplementation. The linter already
found the issues; this module applies the machine-verifiable fixes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Shim detection ────────────────────────────────────────────────────
_SHIM_MARKER = "# lintgate: shim"


def _is_shim_file(filepath: str) -> bool:
    """Detect if a file is a re-export shim that should skip F401 removal.

    A shim file is identified by (checked in order):
    1. Explicit marker: ``# lintgate: shim`` in the first 10 lines
    2. High ``# noqa: F401`` count (>=3 annotations = deliberate re-exports)
    3. ``__all__`` definition + >=2 from-imports
    4. Heuristic: >50% of code lines are ``from ... import`` re-exports
    """
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except OSError:
        return False

    # Check for explicit marker in first 10 lines
    for line in lines[:10]:
        if _SHIM_MARKER in line:
            return True

    # Strong signal: multiple noqa:F401 annotations indicate deliberate re-exports
    noqa_f401_count = sum(
        1 for line in lines if "# noqa" in line and "F401" in line
    )
    if noqa_f401_count >= 3:
        return True

    # Check for __all__ definition (re-export shims typically define __all__)
    has_all_definition = any(
        line.strip().startswith("__all__") and "=" in line for line in lines
    )

    # Heuristic: count from-import lines vs total code lines
    code_lines: list[str] = []
    in_docstring = False
    for line in lines:
        stripped = line.strip()
        # Rough docstring tracking (handles most cases)
        if '"""' in stripped or "'''" in stripped:
            count = stripped.count('"""') + stripped.count("'''")
            if count % 2 == 1:
                in_docstring = not in_docstring
            continue
        if in_docstring or not stripped or stripped.startswith("#"):
            continue
        code_lines.append(stripped)

    reexport_count = sum(
        1 for line in code_lines if line.startswith("from ") and "import" in line
    )

    if len(code_lines) < 2:
        return has_all_definition

    # Moderate signal: __all__ + some from-imports
    if has_all_definition and reexport_count >= 2:
        return True

    return reexport_count / len(code_lines) > 0.5


def _build_shim_ignores(files: list[str]) -> list[str]:
    """Build ruff --extend-per-file-ignores args for detected shim files."""
    args: list[str] = []
    for f in files:
        if _is_shim_file(f):
            args.extend(["--extend-per-file-ignores", f"{f}:F401"])
    return args


@dataclass
class FixResult:
    """Result of running safe fixes."""

    files_modified: list[str] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)
    diff_preview: str = ""
    file_diffs: list[dict[str, str]] = field(default_factory=list)
    dry_run: bool = True
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "files_modified": len(self.files_modified),
            "changes": self.changes,
            "dry_run": self.dry_run,
        }
        if self.diff_preview:
            d["diff_preview"] = self.diff_preview
        if self.file_diffs:
            d["file_diffs"] = self.file_diffs
        if self.errors:
            d["errors"] = self.errors
        if not self.dry_run:
            d["files_modified_list"] = self.files_modified
        return d


def _find_venv_bin(project_root: str) -> str | None:
    """Probe for a virtual environment bin directory."""
    for venv_name in (".venv", "venv", "env"):
        bin_dir = Path(project_root) / venv_name / "bin"
        if bin_dir.is_dir():
            return str(bin_dir)
    return None


def _resolve_ruff(project_root: str) -> str | None:
    """Find ruff executable, preferring project venv."""
    venv_bin = _find_venv_bin(project_root)
    if venv_bin:
        venv_ruff = Path(venv_bin) / "ruff"
        if venv_ruff.exists():
            return str(venv_ruff)
    return shutil.which("ruff")


def run_safe_fixes(
    files: list[str],
    project_root: str,
    dry_run: bool = True,
    safe_only: bool = True,
    fix_imports: bool = True,
) -> FixResult:
    """Run safe auto-fixes on the specified files.

    Args:
        files: List of file paths to fix.
        project_root: Project root directory.
        dry_run: If True, preview changes without modifying files.
        safe_only: If True, only apply ruff's safe fix rules.
        fix_imports: If True, also sort imports (isort via ruff).

    Returns:
        FixResult with changes list and optional diff preview.
    """
    result = FixResult(dry_run=dry_run)
    ruff = _resolve_ruff(project_root)

    if not ruff:
        result.errors.append("ruff not found — install with: pip install ruff")
        return result

    # Filter to existing Python files
    py_files = [f for f in files if f.endswith(".py") and os.path.exists(f)]
    if not py_files:
        return result

    # Build env with venv PATH
    env = os.environ.copy()
    venv_bin = _find_venv_bin(project_root)
    if venv_bin:
        env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")

    if dry_run:
        # Preview mode: collect diffs without applying
        result.diff_preview = _preview_fixes(
            ruff, py_files, project_root, safe_only, fix_imports, env
        )
        # Split into per-file segments for structured output
        if result.diff_preview:
            result.file_diffs = _split_diff_by_file(result.diff_preview)
            for fd in result.file_diffs:
                diff_lines = fd["diff"].splitlines()
                additions = sum(
                    1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++")
                )
                deletions = sum(
                    1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---")
                )
                content_lines = [
                    ln
                    for ln in diff_lines
                    if not ln.startswith(("---", "+++", "@@"))
                    and (ln.startswith("+") or ln.startswith("-"))
                ][:5]
                result.changes.append(
                    {
                        "action": "preview",
                        "file": fd["file"],
                        "additions": additions,
                        "deletions": deletions,
                        "sample": content_lines,
                    }
                )
    else:
        # Apply mode: actually modify files — track via mtime comparison
        before_mtimes = _snapshot_mtimes(py_files)
        _apply_ruff_fix(ruff, py_files, project_root, safe_only, env, result)
        if fix_imports:
            _apply_import_sort(ruff, py_files, project_root, env, result)
        _apply_ruff_format(ruff, py_files, project_root, env, result)
        after_mtimes = _snapshot_mtimes(py_files)
        _collect_modified_files(before_mtimes, after_mtimes, result)

    return result


def _preview_fixes(
    ruff: str,
    files: list[str],
    cwd: str,
    safe_only: bool,
    fix_imports: bool,
    env: dict,
) -> str:
    """Get a diff preview of what fixes would be applied."""
    parts: list[str] = []

    # ruff check --diff
    cmd = [ruff, "check", "--diff"]
    if not safe_only:
        cmd.append("--unsafe-fixes")
    cmd.extend(_build_shim_ignores(files))
    cmd.extend(files)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=30, env=env)
        if proc.stdout.strip():
            parts.append("=== ruff check --fix diff ===")
            # Limit diff to first 2000 chars
            parts.append(proc.stdout[:2000])
    except (subprocess.TimeoutExpired, OSError):
        pass

    # Import sort diff
    if fix_imports:
        cmd = [ruff, "check", "--select", "I", "--diff"] + files
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=15, env=env)
            if proc.stdout.strip():
                parts.append("=== import sort diff ===")
                parts.append(proc.stdout[:1000])
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Format diff
    cmd = [ruff, "format", "--diff"] + files
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=15, env=env)
        if proc.stdout.strip():
            parts.append("=== ruff format diff ===")
            parts.append(proc.stdout[:1000])
    except (subprocess.TimeoutExpired, OSError):
        pass

    return "\n".join(parts) if parts else ""


def _apply_ruff_fix(
    ruff: str,
    files: list[str],
    cwd: str,
    safe_only: bool,
    env: dict,
    result: FixResult,
) -> None:
    """Apply ruff check --fix."""
    cmd = [ruff, "check", "--fix"]
    if not safe_only:
        cmd.append("--unsafe-fixes")
    cmd.extend(_build_shim_ignores(files))
    cmd.extend(files)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=30, env=env)
        # ruff returns 0 (all fixed) or 1 (some unfixed remain) — both are valid
        if proc.returncode in (0, 1):
            # Parse "Found X errors (Y fixed, Z remaining)." from stdout
            _parse_ruff_fix_summary(proc.stdout, result)
    except subprocess.TimeoutExpired:
        result.errors.append("ruff check --fix timed out")
    except OSError as e:
        result.errors.append(f"ruff check --fix failed: {e}")


def _apply_import_sort(
    ruff: str,
    files: list[str],
    cwd: str,
    env: dict,
    result: FixResult,
) -> None:
    """Sort imports via ruff."""
    cmd = [ruff, "check", "--select", "I", "--fix"] + files
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=15, env=env)
        if proc.returncode in (0, 1):
            _parse_ruff_fix_summary(proc.stdout, result, action="import_sort")
    except (subprocess.TimeoutExpired, OSError):
        pass


def _apply_ruff_format(
    ruff: str,
    files: list[str],
    cwd: str,
    env: dict,
    result: FixResult,
) -> None:
    """Apply ruff format."""
    cmd = [ruff, "format"] + files
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=15, env=env)
        if proc.returncode == 0:
            # ruff format reports "1 file reformatted" on stderr
            for line in proc.stderr.splitlines():
                if "reformatted" in line.lower():
                    result.changes.append({"action": "format", "detail": line.strip()[:120]})
    except (subprocess.TimeoutExpired, OSError):
        pass


def _snapshot_mtimes(files: list[str]) -> dict[str, float]:
    """Capture modification times for a list of files."""
    import contextlib

    mtimes: dict[str, float] = {}
    for f in files:
        with contextlib.suppress(OSError):
            mtimes[f] = os.path.getmtime(f)
    return mtimes


def _collect_modified_files(
    before: dict[str, float],
    after: dict[str, float],
    result: FixResult,
) -> None:
    """Compare mtime snapshots and record modified files."""
    for f, new_mtime in after.items():
        old_mtime = before.get(f)
        if old_mtime is not None and new_mtime > old_mtime and f not in result.files_modified:
            result.files_modified.append(f)


def _split_diff_by_file(diff_text: str) -> list[dict[str, str]]:
    """Split a unified diff into per-file segments for structured output."""
    segments: list[dict[str, str]] = []
    current_file: str | None = None
    current_lines: list[str] = []

    for line in diff_text.splitlines():
        if line.startswith("--- "):
            if current_file and current_lines:
                segments.append({"file": current_file, "diff": "\n".join(current_lines)})
            # Extract filename: strip "--- a/" or "--- " prefix
            raw = line[4:]
            if raw.startswith("a/"):
                raw = raw[2:]
            current_file = raw.split("\t")[0]  # Strip timestamp if present
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_file and current_lines:
        segments.append({"file": current_file, "diff": "\n".join(current_lines)})

    return segments


def _parse_ruff_fix_summary(
    stdout: str,
    result: FixResult,
    action: str = "ruff_fix",
) -> None:
    """Parse ruff's 'Found X errors (Y fixed, Z remaining).' summary line.

    Also captures 'Fixed N errors [N ...]' style output from newer ruff versions.
    """
    import re

    for line in stdout.splitlines():
        line = line.strip()
        # Match: "Found 4 errors (3 fixed, 1 remaining)."
        m = re.match(r"Found \d+ errors? \((\d+) fixed", line)
        if m and int(m.group(1)) > 0:
            result.changes.append({"action": action, "detail": line[:120]})
            return
        # Match: "Fixed 3 errors [F401, F841, ...]."
        m2 = re.match(r"Fixed (\d+) errors?", line)
        if m2 and int(m2.group(1)) > 0:
            result.changes.append({"action": action, "detail": line[:120]})
            return

"""Linter protocol and base class.

Pattern borrowed from TailChasingFixer's BaseAnalyzer: a protocol-based
interface where each linter implements run(ctx) -> Iterable[LintIssue].

Graceful degradation via shutil.which() from ARC_AGI_3's lint.py.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..types import LinterContext, LinterResult, LintIssue

if TYPE_CHECKING:
    from collections.abc import Iterable


def _find_venv_bin(project_root: str | None) -> str | None:
    """Probe for a virtual environment bin directory in the project."""
    if not project_root:
        return None
    for venv_name in (".venv", "venv", "env"):
        bin_dir = Path(project_root) / venv_name / "bin"
        if bin_dir.is_dir():
            return str(bin_dir)
    return None


def _resolve_executable(name: str, project_root: str | None = None) -> str | None:
    """Resolve an executable, preferring project venv over system PATH."""
    venv_bin = _find_venv_bin(project_root)
    if venv_bin:
        venv_path = Path(venv_bin) / name
        if venv_path.exists() and venv_path.is_file():
            return str(venv_path)
    return shutil.which(name)


@runtime_checkable
class Linter(Protocol):
    """Protocol for all linters.

    Any tool that finds code issues implements this interface.
    Modeled after TailChasingFixer's Analyzer protocol.
    """

    name: str
    tier: int  # 0-3, determines when this linter fires
    timeout_ms: int  # Per-linter timeout
    required_tool: str | None  # Executable name (e.g., "ruff"), None if built-in

    def available(self) -> bool:
        """Check if this linter's required tool is installed."""
        ...

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Run the linter and yield issues found."""
        ...


class BaseLinter:
    """Convenience base class implementing common patterns.

    Subclasses only need to implement run().
    """

    name: str = "unnamed"
    tier: int = 0
    timeout_ms: int = 5000
    required_tool: str | None = None

    def available(self, project_root: str | None = None) -> bool:
        """Check if the required external tool is installed.

        Checks project venv first, then falls back to system PATH.
        """
        if self.required_tool is None:
            return True  # Built-in, always available
        return _resolve_executable(self.required_tool, project_root) is not None

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Override in subclass to yield LintIssues."""
        raise NotImplementedError

    def run_command(
        self,
        cmd: list[str],
        cwd: str,
        timeout_s: float | None = None,
    ) -> subprocess.CompletedProcess:
        """Run an external command with timeout protection.

        Resolves the command executable through venv if available.
        Returns CompletedProcess. Raises subprocess.TimeoutExpired
        if the command exceeds the timeout.
        """
        if timeout_s is None:
            timeout_s = self.timeout_ms / 1000.0

        # Resolve executable through venv if available
        resolved_cmd = list(cmd)
        if cmd:
            resolved = _resolve_executable(cmd[0], cwd)
            if resolved:
                resolved_cmd[0] = resolved

        # Prepend venv bin to PATH so subprocess children also find venv tools
        env = None
        venv_bin = _find_venv_bin(cwd)
        if venv_bin:
            env = os.environ.copy()
            env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")

        return subprocess.run(
            resolved_cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout_s,
            env=env,
        )

    def execute(self, ctx: LinterContext) -> LinterResult:
        """Run this linter with full error handling.

        Returns a LinterResult with status, issues, and timing.
        This is the method called by lint_runner.py.
        """
        if not self.available(project_root=ctx.project_root):
            return LinterResult(
                linter_name=self.name,
                status="skipped",
                error=f"{self.required_tool} not installed",
            )

        # Filter to files this linter cares about
        files = self._filter_files(ctx.files)
        if not files:
            return LinterResult(
                linter_name=self.name,
                status="skipped",
                error="No applicable files",
            )

        # Create a context scoped to this linter's files
        scoped_ctx = LinterContext(
            files=files,
            project_root=ctx.project_root,
            strictness=ctx.strictness,
            config=ctx.config.get(self.name, {}),
        )

        start = time.perf_counter()
        try:
            issues = list(self.run(scoped_ctx))
            duration_ms = (time.perf_counter() - start) * 1000
            return LinterResult(
                linter_name=self.name,
                issues=issues,
                status="ok",
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired:
            duration_ms = (time.perf_counter() - start) * 1000
            return LinterResult(
                linter_name=self.name,
                status="timeout",
                error=f"Timed out after {self.timeout_ms}ms",
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            return LinterResult(
                linter_name=self.name,
                status="error",
                error=f"{type(e).__name__}: {e}",
                duration_ms=duration_ms,
            )

    def _filter_files(self, files: list[str]) -> list[str]:
        """Filter files to those this linter should process.

        Override in subclass for language-specific filtering.
        Default: only Python files.
        """
        return [f for f in files if f.endswith(".py")]

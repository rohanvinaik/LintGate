"""Custom linter runner — executes user-defined scripts.

Allows projects to integrate their own analysis tools via lintgate.yaml:

```yaml
linters:
  custom_tailchasing:
    enabled: true
    command: "python -m tailchasing.cli --json src/"
    tier: 3
    severity_default: "warning"
    parse_mode: "jsonl"  # or "lines" for plain text
```

Supports two parse modes:
- "jsonl": Each line of stdout is a JSON object with fields:
  file, line, message, kind, severity (optional)
- "lines": Each line of stdout is a plain text finding, reported as-is

The custom linter is a bridge to bring project-specific tools (like
TailChasingFixer or ShortcutForge's DSL linter) into the LintGate
pipeline without modifying LintGate itself.
"""

from __future__ import annotations

import json
import shlex
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable


class CustomLinter(BaseLinter):
    """Custom linter — runs a user-defined command and parses output.

    Configured via lintgate.yaml. Not auto-discovered — must be
    explicitly listed in config with a command.
    """

    name = "custom"
    tier = 3
    timeout_ms = 15000
    required_tool = None  # Availability checked via command existence

    def __init__(
        self,
        linter_name: str,
        command: str,
        tier: int = 3,
        severity_default: str = "warning",
        parse_mode: str = "lines",
        timeout_ms: int = 15000,
    ):
        self.name = linter_name
        self._command = command
        self.tier = tier
        self._severity_default = severity_default
        self._parse_mode = parse_mode
        self.timeout_ms = timeout_ms

    def available(self) -> bool:
        """Check if the custom command's first token is an executable."""
        import shutil

        try:
            parts = shlex.split(self._command)
            first = parts[0] if parts else ""
            # Check if it's python/python3 (always available) or an executable
            if first in ("python", "python3"):
                return True
            return shutil.which(first) is not None
        except (ValueError, IndexError):
            return False

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Run the custom command and parse output."""

        cmd = shlex.split(self._command)
        result = self.run_command(cmd, ctx.project_root)

        output = result.stdout or ""
        if not output.strip():
            return

        if self._parse_mode == "jsonl":
            yield from self._parse_jsonl(output)
        else:
            yield from self._parse_lines(output)

    def _parse_jsonl(self, output: str) -> Iterable[LintIssue]:
        """Parse JSON Lines output (one JSON object per line)."""
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue

            yield LintIssue(
                linter=self.name,
                kind=item.get("kind", "custom"),
                message=item.get("message", line),
                file=item.get("file"),
                line=item.get("line"),
                column=item.get("column"),
                severity=item.get("severity", self._severity_default),
                confidence=item.get("confidence", 0.8),
                suggestions=item.get("suggestions", []),
            )

    def _parse_lines(self, output: str) -> Iterable[LintIssue]:
        """Parse plain text output (one finding per line)."""
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue

            yield LintIssue(
                linter=self.name,
                kind="custom",
                message=line,
                severity=self._severity_default,
                confidence=0.7,  # Lower confidence for unstructured output
            )

    def _filter_files(self, files: list[str]) -> list[str]:
        """Custom linters apply to all files — the command handles filtering."""
        return files

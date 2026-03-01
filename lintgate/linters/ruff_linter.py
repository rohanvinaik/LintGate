"""Ruff linter integration.

Tier 0 — always runs. Uses ruff's JSON output mode for structured results.
Ruff replaces the entire flake8/isort/pyflakes/pycodestyle ecosystem.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable

# Ruff error codes that should be blocking (prevent proceeding)
_BLOCKING_CODES = frozenset(
    {
        # Pyflakes errors (undefined names, unused imports in certain contexts)
        "F821",  # Undefined name
        "F811",  # Redefinition of unused name
        "F841",  # Local variable assigned but never used (in strict mode)
        # Syntax errors
        "E999",  # Syntax error
    }
)

# Ruff error codes that are informational (learning signal, not actionable)
_INFORMATIONAL_CODES = frozenset(
    {
        "E501",  # Line too long
        "W291",  # Trailing whitespace
        "W292",  # No newline at end of file
        "W293",  # Whitespace before ':'
        "D100",  # Missing docstring in public module
        "D101",  # Missing docstring in public class
        "D102",  # Missing docstring in public method
        "D103",  # Missing docstring in public function
    }
)


class RuffLinter(BaseLinter):
    """Ruff linter — fast Python linting with JSON output.

    Always available as tier 0. Uses --output-format json for structured
    results that the agent can parse without ANSI scraping.
    """

    name = "ruff_check"
    tier = 0
    timeout_ms = 5000
    required_tool = "ruff"

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Run ruff check with JSON output on specified files."""

        cmd = [
            "ruff",
            "check",
            "--output-format",
            "json",
            "--no-fix",  # Don't auto-fix, just report
        ]

        # Add extra args from config
        extra_args = ctx.config.get("extra_args", [])
        if extra_args:
            cmd.extend(extra_args)

        cmd.extend(ctx.files)

        result = self.run_command(cmd, ctx.project_root)

        # Ruff outputs JSON to stdout even on failure (exit code 1 = issues found)
        if result.stdout:
            try:
                items = json.loads(result.stdout)
            except json.JSONDecodeError:
                return  # Malformed output, skip

            for item in items:
                code = item.get("code", "unknown")
                location = item.get("location", {})
                end_location = item.get("end_location", {})
                fix = item.get("fix")

                # E402 evidence attachment — transitive import analysis
                evidence: dict = {}
                confidence = 1.0  # Ruff is deterministic
                if code == "E402":
                    evidence = _build_e402_evidence_safe(
                        item,
                        location,
                        ctx.project_root,
                    )
                    # Conditionally escalate confidence for cross-env risk (Gap 6)
                    new_conf, escalation = _maybe_escalate_e402(evidence, confidence)
                    confidence = new_conf
                    if escalation:
                        evidence["escalation"] = escalation

                yield LintIssue(
                    linter="ruff",
                    kind=code,
                    message=item.get("message", ""),
                    file=item.get("filename"),
                    line=location.get("row"),
                    column=location.get("column"),
                    end_line=end_location.get("row"),
                    end_column=end_location.get("column"),
                    severity=_classify_severity(code, ctx.strictness),
                    confidence=confidence,
                    fixable=fix is not None,
                    fix_description=fix.get("message") if fix else None,
                    evidence=evidence,
                )


class RuffFormatLinter(BaseLinter):
    """Ruff format checker — checks formatting without fixing.

    Tier 0 complement to RuffLinter. Only checks, doesn't format.
    """

    name = "ruff_format"
    tier = 0
    timeout_ms = 3000
    required_tool = "ruff"

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Run ruff format --check on specified files."""

        cmd = ["ruff", "format", "--check", "--diff"]
        cmd.extend(ctx.files)

        result = self.run_command(cmd, ctx.project_root)

        # ruff format --check exits 1 if files need formatting
        if result.returncode != 0 and result.stdout:
            # Emit one issue per file that needs formatting
            seen_files: set[str] = set()
            for line in result.stdout.splitlines():
                if line.startswith("+++ ") and line != "+++ /dev/null":
                    filepath = line.split("\t")[0].removeprefix("+++ ")
                    if filepath not in seen_files:
                        seen_files.add(filepath)
                        yield LintIssue(
                            linter="ruff_format",
                            kind="format",
                            message="File needs formatting",
                            file=filepath,
                            severity="informational",
                            confidence=1.0,
                            fixable=True,
                            fix_description="Run: ruff format",
                        )


def _maybe_escalate_e402(
    evidence: dict,
    current_confidence: float,
) -> tuple[float, dict | None]:
    """Conditionally boost E402 confidence when cross-environment risk is present.

    Conditions (ALL required):
    1. non_stdlib_deps is non-empty
    2. has_lazy is True (lazy imports hide failures until runtime)

    Returns (new_confidence, escalation_evidence_or_None).
    Severity stays "warning" — only confidence changes.
    """
    transitive = evidence.get("transitive_imports", {})
    non_stdlib = transitive.get("non_stdlib", [])
    has_lazy = transitive.get("has_lazy", False)

    if non_stdlib and has_lazy:
        escalation = {
            "reason": "Mid-file import transitively depends on non-stdlib "
            "packages with lazy import patterns — cross-environment risk",
            "conditions_met": ["non_stdlib_deps", "lazy_imports"],
            "non_stdlib": non_stdlib,
        }
        return (0.85, escalation)  # Boost from default 1.0 to 0.85

    return (current_confidence, None)


def _build_e402_evidence_safe(
    item: dict,
    location: dict,
    project_root: str,
) -> dict:
    """Build E402 transitive import evidence, with graceful degradation.

    Attaches evidence only — does NOT modify severity. The evidence
    informs the agent's decision about import placement.
    """
    try:
        from .structure_checks.import_tracing import build_e402_evidence

        filepath = item.get("filename", "")
        line = location.get("row", 0)

        # Extract module name from ruff's message (e.g. "Module level import not at top of file")
        # Ruff's E402 message doesn't include the module name directly,
        # so we extract it from the source line or filename context.
        # The message format is: "Module level import not at top of file"
        # We need to parse the import from the source file.
        module_name = _extract_e402_module(filepath, line)
        if not module_name:
            return {"code": "E402", "note": "could not resolve module name"}

        return build_e402_evidence(module_name, filepath, line, project_root)
    except Exception:
        return {}  # Graceful degradation — never break the linter


def _extract_e402_module(filepath: str, line: int) -> str | None:
    """Extract the imported module name from the source file at the given line."""
    import ast

    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, OSError):
        return None

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if node.lineno != line:
            continue
        if isinstance(node, ast.Import) and node.names:
            return node.names[0].name
        if isinstance(node, ast.ImportFrom) and node.module:
            return node.module
    return None


def _classify_severity(code: str, strictness: str) -> str:
    """Map ruff error code to LintGate severity.

    In strict mode (pipeline-critical paths), more codes become blocking.
    """
    if code in _BLOCKING_CODES:
        return "blocking"

    if code in _INFORMATIONAL_CODES:
        return "informational"

    # In strict mode, unused imports and variables are warnings
    if strictness == "strict" and (code.startswith("F4") or code.startswith("F8")):
        return "warning"

    return "warning"

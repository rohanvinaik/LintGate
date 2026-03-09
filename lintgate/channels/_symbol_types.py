"""Data structures for the symbol coverage gate.

Extracted to break circular imports between symbol_coverage.py and its
sub-modules. All types are re-exported from symbol_coverage for backward
compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SymbolSpan:
    """A function or method span in a source file."""

    file: str  # Absolute path
    symbol_key: str  # Canonical: "relative/path.py::ClassName.method_name"
    name: str  # "func_name" or "ClassName.method_name"
    start_line: int  # min(first_decorator.lineno, node.lineno)
    end_line: int  # ast.FunctionDef.end_lineno
    is_method: bool
    class_name: str | None


@dataclass
class FileCoverage:
    """Coverage data for a single file."""

    executed_lines: set[int]
    missing_lines: set[int]
    excluded_lines: set[int]
    missing_branches: list[tuple[int, int]]  # (from_line, to_line)


@dataclass
class SymbolCoverageResult:
    """Coverage verdict for a single symbol."""

    symbol: SymbolSpan
    covered: bool  # Binary: no missing lines AND no missing branches
    missing_lines: list[int]
    missing_branches: list[tuple[int, int]]
    total_lines_in_span: int
    executed_lines_in_span: int


@dataclass
class SymbolCoverageWaiver:
    """Explicit per-symbol exemption from coverage gate."""

    symbol: str  # Canonical key
    reason: str  # Required, non-empty
    expires: str | None = None  # ISO date "2025-09-01" or None


@dataclass
class SymbolCoverageGateResult:
    """Aggregate result from the symbol coverage gate."""

    passed: bool
    symbol_results: list[SymbolCoverageResult] = field(default_factory=list)
    waivers_applied: list[tuple[str, SymbolCoverageWaiver]] = field(default_factory=list)
    waivers_expired: list[SymbolCoverageWaiver] = field(default_factory=list)
    skipped_reasons: list[str] = field(default_factory=list)
    unresolved_required: list[str] = field(default_factory=list)

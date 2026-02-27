"""Core data types for LintGate.

Patterns borrowed from:
- TailChasingFixer's Issue (kind, severity, confidence, evidence, suggestions)
- ShortcutForge's LintChange (confidence scoring, categorized repairs)
- ARC_AGI_3's lint.py (blocking/informational severity tiers)
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Any

# ─── Lint Issues ──────────────────────────────────────────────────────────


@dataclass
class LintIssue:
    """A single issue found by a linter.

    Combines patterns from TailChasingFixer's Issue (structured evidence,
    suggestions) and ShortcutForge's LintChange (confidence scoring).
    """

    linter: str  # Which linter found it ("ruff", "mypy", "radon")
    kind: str  # Issue type/code ("F821", "complexity", "import-error")
    message: str  # Human-readable description
    file: str | None = None  # Absolute file path
    line: int | None = None
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None
    severity: str = "warning"  # "blocking", "warning", "informational"
    confidence: float = 1.0  # 0.0-1.0 (deterministic tools = 1.0, fuzzy = lower)
    fixable: bool = False  # Can be auto-fixed
    fix_description: str | None = None
    proven_resolution: dict[str, Any] | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)
    issue_id: str = ""  # Deterministic hash — set by results_aggregator after dedup

    def compute_issue_id(self) -> str:
        """Generate deterministic issue ID from linter|kind|file|line|message."""
        raw = f"{self.linter}|{self.kind}|{self.file or ''}|{self.line or 0}|{(self.message or '')[:50]}"
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict, omitting None/empty fields for compact JSON."""
        return {
            k: v
            for k, v in self.__dict__.items()
            if v is not None and v != [] and v != {} and v != ""
        }

    def short_location(self) -> str:
        """Short file:line reference for display."""
        if not self.file:
            return ""
        basename = os.path.basename(self.file)
        if self.line:
            return f"{basename}:{self.line}"
        return basename

    def __hash__(self) -> int:
        return hash((self.linter, self.kind, self.file, self.line))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LintIssue):
            return False
        return (
            self.linter == other.linter
            and self.kind == other.kind
            and self.file == other.file
            and self.line == other.line
        )


# ─── Change Classification ───────────────────────────────────────────────

# Change kinds (what category of change was this?)
CHANGE_KINDS = frozenset(
    {
        "import",  # Only import statements changed
        "config",  # Config files (yaml, toml, json, .env)
        "logic",  # Business logic / implementation code
        "test",  # Test files
        "docs",  # Documentation files
        "structural",  # Class/function definitions added/removed/renamed
        "dependency",  # Dependency files (requirements.txt, package.json)
        "build",  # Build commands (pip install, npm install, etc.)
    }
)

# Risk levels (how dangerous is this change?)
RISK_LEVELS = frozenset(
    {
        "none",  # No actual change (failed bash command, read-only)
        "cosmetic",  # Docs, comments, formatting only
        "moderate",  # Normal code changes
        "structural",  # Function/class signatures changed, new files
        "architectural",  # Pipeline-critical paths, cross-file structural changes
    }
)


@dataclass
class DiffAnalysis:
    """Analysis of what actually changed in the text diff."""

    lines_added: int = 0
    lines_removed: int = 0
    import_only: bool = False
    function_signatures_changed: bool = False
    class_structure_changed: bool = False
    is_new_file: bool = False
    formatting_only: bool = False
    is_build_command: bool = False

    @classmethod
    def empty(cls) -> DiffAnalysis:
        return cls()


@dataclass
class ChangeClassification:
    """Result of classifying what just changed.

    Produced by change_classifier.py, consumed by tier_selector.py.
    """

    files_changed: list[str] = field(default_factory=list)
    files_by_language: dict[str, list[str]] = field(default_factory=dict)
    change_kind: str = "logic"
    risk_level: str = "moderate"
    import_only: bool = False
    function_signatures_changed: bool = False
    class_structure_changed: bool = False
    touches_pipeline_critical: bool = False
    touches_test_files: bool = False
    is_new_file: bool = False
    lines_added: int = 0
    lines_removed: int = 0
    tool_name: str = ""
    timestamp: float = field(default_factory=time.time)


# ─── Lint Tiers ───────────────────────────────────────────────────────────


@dataclass
class LintTier:
    """Selected lint tier — which linters to run and why.

    Produced by tier_selector.py, consumed by lint_runner.py.
    """

    name: str  # e.g. "tier_2_logic"
    linters: list[str]  # Linter registry names
    files: list[str]  # Files to lint (scoped, not whole project)
    reason: str  # Why this tier was selected
    strictness: str = "normal"  # "relaxed" for tests, "strict" for pipeline-critical
    skip: bool = False  # True if we should skip linting entirely


# Sentinel for "don't lint at all"
SKIP_TIER = LintTier(name="skip", linters=[], files=[], reason="", skip=True)


# ─── Linter Context and Results ──────────────────────────────────────────


@dataclass
class LinterContext:
    """Context passed to each linter's run() method."""

    files: list[str]  # Files to lint
    project_root: str  # Project root directory
    strictness: str = "normal"  # "relaxed", "normal", "strict"
    config: dict[str, Any] = field(default_factory=dict)  # Linter-specific config


@dataclass
class LinterResult:
    """Result from a single linter run."""

    linter_name: str
    issues: list[LintIssue] = field(default_factory=list)
    status: str = "ok"  # "ok", "timeout", "error", "skipped"
    error: str | None = None
    duration_ms: float = 0.0


# ─── Aggregated Results ──────────────────────────────────────────────────


@dataclass
class AggregatedResult:
    """Aggregated results from all linters — the final pipeline output."""

    blocking: list[LintIssue] = field(default_factory=list)
    warnings: list[LintIssue] = field(default_factory=list)
    informational: list[LintIssue] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    linter_statuses: dict[str, str] = field(default_factory=dict)
    tier_used: str = ""
    tier_reason: str = ""
    total_duration_ms: float = 0.0
    files_linted: list[str] = field(default_factory=list)


# ─── Quality Policy ──────────────────────────────────────────────────────


@dataclass
class CoveragePolicy:
    """Coverage enforcement thresholds."""

    global_threshold: int = 80  # --cov-fail-under
    diff_threshold: int = 80  # diff-cover --fail-under
    source_packages: list[str] = field(default_factory=lambda: ["lintgate", "mcp_tools"])


@dataclass
class ToleratedFalsePositive:
    """A security finding known to be a false positive."""

    rule: str = ""  # e.g. "pythonsecurity:S2083"
    file: str = ""  # e.g. "lintgate/reset.py"
    scope: str = ""  # e.g. "**/*.py" (alternative to file)
    reason: str = ""


@dataclass
class SecurityPolicy:
    """Security gate policy."""

    tolerated_false_positives: list[ToleratedFalsePositive] = field(default_factory=list)


@dataclass
class QualityPolicy:
    """Single source of truth for quality gate thresholds.

    Loaded from .claude/lintgate.yaml quality_policy section.
    Consumed by CI workflows, test channel, and telemetry.
    """

    coverage: CoveragePolicy = field(default_factory=CoveragePolicy)
    security: SecurityPolicy = field(default_factory=SecurityPolicy)


# ─── Project Configuration ───────────────────────────────────────────────


@dataclass
class ProjectConfig:
    """Per-project configuration, loaded from .claude/lintgate.yaml or auto-detected."""

    project_root: str = ""
    languages: list[str] = field(default_factory=list)
    enabled_linters: dict[str, bool] = field(default_factory=dict)
    linter_configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    pipeline_critical_paths: list[str] = field(default_factory=list)
    severity_overrides: dict[str, str] = field(default_factory=dict)
    exemptions: dict[str, dict[str, Any]] = field(default_factory=dict)
    extra_tier3_linters: list[str] = field(default_factory=list)
    tool_version_requirements: dict[str, str] = field(default_factory=dict)
    enforced_optional_groups: list[str] = field(default_factory=list)
    path_policies: list[dict[str, Any]] = field(default_factory=list)
    debounce: dict[str, float] = field(
        default_factory=lambda: {
            "tier_0": 0.0,
            "tier_1": 2.0,
            "tier_2": 3.0,
            "tier_3": 5.0,
        }
    )
    total_timeout_ms: int = 8000
    quality_policy: QualityPolicy = field(default_factory=QualityPolicy)

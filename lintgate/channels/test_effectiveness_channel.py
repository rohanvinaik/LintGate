"""Test effectiveness channel — mutation-informed assertion quality for ControlPlane.

Co-equal mesh participant that measures test *effectiveness* (assertion quality)
as distinct from test *coverage* (line execution). Inspired by mutation testing
data showing 59% kill rate despite 92% line coverage.

Seven finding codes (TEFF001–TEFF007):
- TEFF001: Low semantic assertion ratio (file-level)
- TEFF002: Untested public function
- TEFF003: Structural-only assertions (function tested with only weak assertions)
- TEFF004: High mutation vulnerability (function-level)
- TEFF005: Pure function, weak tests (cross-channel with performance)
- TEFF006: Covered but vulnerable (cross-channel with symbol coverage)
- TEFF007: Complex but weakly tested (cross-channel with radon)

TEFF005 is the key holographic signal — it composes purity analysis with
effectiveness analysis. Pure functions are the easiest to test thoroughly,
so weak tests on pure functions represent maximum wasted opportunity.
"""

from __future__ import annotations

import os
import time
from typing import Any, Literal

from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    SupervisionEvent,
)
from lintgate.linters.test_effectiveness.manifest import (
    build_test_effectiveness_manifest,
)
from lintgate.linters.test_effectiveness.types import (
    SEMANTIC_STRENGTH_THRESHOLD,
    TestEffectivenessManifest,
)
from lintgate.types import LintIssue

# Cap per-code findings to avoid flooding
_TOP_N_FINDINGS = 5

# Sampling thresholds (#203)
_MAX_TEST_FILES_DEFAULT = 200
_ESTIMATED_PER_FILE_SECONDS = 0.3


def _discover_python_files(project_root: str) -> list[str]:
    """Discover Python files (reuse structure channel's discovery)."""
    from lintgate.channels.structure_channel import _discover_python_files as discover

    return discover(project_root)


def _discover_test_files(project_root: str) -> list[str]:
    """Discover test files."""
    from lintgate.linters.test_effectiveness.test_analyzer import (
        _discover_test_files as discover,
    )

    return discover(project_root)


def _select_test_files_for_analysis(
    all_test_files: list[str],
    project_root: str,
    budget_seconds: float = 90.0,
    estimated_per_file_seconds: float = _ESTIMATED_PER_FILE_SECONDS,
) -> tuple[list[str], bool]:
    """Select a representative sample of test files within budget.

    Returns (selected_files, was_sampled).

    Priority sampling order:
    1. Recently changed test files (git)
    2. Largest test files (most likely to have quality issues)
    3. Remaining in alphabetical order
    """
    max_files = int(budget_seconds / estimated_per_file_seconds)

    if len(all_test_files) <= max_files:
        return all_test_files, False

    selected: list[str] = []
    selected_set: set[str] = set()

    def _add(path: str) -> None:
        if path not in selected_set and len(selected) < max_files:
            selected.append(path)
            selected_set.add(path)

    # Priority 1: recently changed test files
    try:
        import subprocess

        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~5"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            changed = set(result.stdout.strip().splitlines())
            for tf in all_test_files:
                rel = os.path.relpath(tf, project_root)
                if rel in changed:
                    _add(tf)
    except Exception:
        pass

    # Priority 2: largest test files
    try:
        by_size = sorted(
            all_test_files,
            key=lambda f: os.path.getsize(f),
            reverse=True,
        )
        for tf in by_size:
            _add(tf)
    except Exception:
        # Fallback: just take first N
        for tf in all_test_files:
            _add(tf)

    return selected, True


# ── Finding generators ────────────────────────────────────────────────


def _teff001_low_semantic_ratio(
    manifest: TestEffectivenessManifest,
    project_root: str,
) -> list[LintIssue]:
    """TEFF001 — File-level low semantic assertion ratio."""
    # Project-level check: overall semantic ratio
    if not manifest.functions:
        return []

    total_assertions = 0
    semantic_assertions = 0
    for fe in manifest.functions.values():
        for a in fe.assertions:
            total_assertions += 1
            if a.strength >= SEMANTIC_STRENGTH_THRESHOLD:
                semantic_assertions += 1

    if total_assertions == 0:
        return []

    ratio = semantic_assertions / total_assertions
    if ratio >= 0.4:
        return []

    return [
        LintIssue(
            linter="test_effectiveness",
            kind="TEFF001",
            message=(
                f"Low semantic assertion ratio ({ratio:.1%}). "
                f"Only {semantic_assertions} of {total_assertions} assertions "
                f"use value-checking patterns (equality, comparison, length). "
                f"Structural assertions (is_none, is_true) let value-altering mutants survive."
            ),
            file=project_root,
            severity="informational",
            confidence=0.85,
            evidence={
                "code": "TEFF001",
                "semantic_assertions": semantic_assertions,
                "total_assertions": total_assertions,
                "ratio": round(ratio, 3),
            },
            suggestions=[
                "Replace `assert result is not None` with `assert result == expected_value`",
                "Replace `assert x` with `assert x == expected` for exact value checks",
            ],
        )
    ]


def _teff002_untested_functions(
    manifest: TestEffectivenessManifest,
    project_root: str,
) -> list[LintIssue]:
    """TEFF002 — Untested public functions."""
    untested = [
        name
        for name, fe in manifest.functions.items()
        if fe.test_count == 0 and not name.startswith("_")
    ]

    if not untested:
        return []

    top = untested[:_TOP_N_FINDINGS]
    remaining = len(untested) - len(top)
    names_str = ", ".join(f"'{f}'" for f in top)
    suffix = f" and {remaining} more" if remaining > 0 else ""

    return [
        LintIssue(
            linter="test_effectiveness",
            kind="TEFF002",
            message=(
                f"{len(untested)} public functions have no mapped tests: {names_str}{suffix}."
            ),
            file=project_root,
            severity="informational",
            confidence=0.7,
            evidence={
                "code": "TEFF002",
                "untested_count": len(untested),
                "untested_functions": untested[:10],
            },
            suggestions=[
                "Use `controlplane_test_skeleton` to generate test stubs for untested functions",
            ],
        )
    ]


def _teff003_structural_only(
    manifest: TestEffectivenessManifest,
    project_root: str,
) -> list[LintIssue]:
    """TEFF003 — Functions tested but only with structural assertions."""
    findings: list[LintIssue] = []
    count = 0

    for name, fe in manifest.functions.items():
        if fe.test_count == 0 or not fe.assertions:
            continue
        # All assertions are structural (weak)
        if all(a.strength < SEMANTIC_STRENGTH_THRESHOLD for a in fe.assertions):
            count += 1
            if count <= _TOP_N_FINDINGS:
                findings.append(
                    LintIssue(
                        linter="test_effectiveness",
                        kind="TEFF003",
                        message=(
                            f"'{name}' is tested but all {len(fe.assertions)} assertions are "
                            f"structural (is_none, is_true, isinstance). "
                            f"These let value-altering mutants like find→rfind survive."
                        ),
                        file=project_root,
                        severity="warning",
                        confidence=0.8,
                        evidence={
                            "code": "TEFF003",
                            "function": name,
                            "assertion_count": len(fe.assertions),
                            "assertion_kinds": [a.kind.value for a in fe.assertions],
                        },
                        suggestions=[
                            f"Add `assert {name}(...) == expected_value` with exact expected outputs",
                            "Check return values, not just existence/type",
                        ],
                    )
                )

    return findings


def _teff004_high_vulnerability(
    manifest: TestEffectivenessManifest,
    project_root: str,
) -> list[LintIssue]:
    """TEFF004 — Functions with high mutation vulnerability."""
    findings: list[LintIssue] = []

    vulnerable = sorted(
        (
            (n, f)
            for n, f in manifest.functions.items()
            if f.mutation_vulnerability > 0.7 and f.test_count > 0
        ),
        key=lambda x: x[1].mutation_vulnerability,
        reverse=True,
    )

    for count, (name, fe) in enumerate(vulnerable, 1):
        if count <= _TOP_N_FINDINGS:
            findings.append(
                LintIssue(
                    linter="test_effectiveness",
                    kind="TEFF004",
                    message=(
                        f"'{name}' has high mutation vulnerability "
                        f"({fe.mutation_vulnerability:.1%}). "
                        f"Effectiveness score: {fe.effectiveness_score:.1%} "
                        f"from {len(fe.assertions)} assertions across {fe.test_count} tests."
                    ),
                    file=project_root,
                    severity="warning",
                    confidence=0.75,
                    evidence={
                        "code": "TEFF004",
                        "function": name,
                        "mutation_vulnerability": round(fe.mutation_vulnerability, 3),
                        "effectiveness_score": round(fe.effectiveness_score, 3),
                        "test_count": fe.test_count,
                    },
                    suggestions=[
                        "Add equality assertions that check exact return values",
                        "Add boundary tests for edge cases (0, -1, empty string)",
                    ],
                )
            )

    return findings


def _teff005_pure_weak_tests(
    manifest: TestEffectivenessManifest,
    project_root: str,
    py_files: list[str],
) -> list[LintIssue]:
    """TEFF005 — Pure function with weak tests (cross-channel with performance)."""
    try:
        from lintgate.linters.performance_checks.manifest import build_manifest

        perf_manifest = build_manifest(project_root, py_files)
    except Exception:
        return []

    pure_names = perf_manifest.get_pure_function_names()
    if not pure_names:
        return []

    findings: list[LintIssue] = []
    count = 0

    for name in sorted(pure_names):
        fe = manifest.functions.get(name)
        if not fe or fe.test_count == 0:
            continue
        if fe.effectiveness_score >= 0.5:
            continue

        count += 1
        if count <= _TOP_N_FINDINGS:
            findings.append(
                LintIssue(
                    linter="test_effectiveness",
                    kind="TEFF005",
                    message=(
                        f"'{name}' is mathematically pure but tests are weak "
                        f"(effectiveness {fe.effectiveness_score:.1%}). "
                        f"Pure functions are the easiest to test thoroughly — "
                        f"this is maximum wasted opportunity."
                    ),
                    file=project_root,
                    severity="warning",
                    confidence=0.85,
                    evidence={
                        "code": "TEFF005",
                        "function": name,
                        "is_pure": True,
                        "effectiveness_score": round(fe.effectiveness_score, 3),
                        "semantic_ratio": round(fe.semantic_ratio, 3),
                    },
                    suggestions=[
                        f"Add property-based test: @given(st.integers()) for '{name}'",
                        "Use generate_property_tests tool for Hypothesis templates",
                        "Pure functions guarantee: same input → same output. Test exact values.",
                    ],
                )
            )

    return findings


def _analyze_complexity_block(
    block: Any,
    manifest: TestEffectivenessManifest,
    filepath: str,
    project_root: str,
) -> LintIssue | None:
    """Analyze a single radon complexity block for weak test coverage."""
    if getattr(block, "complexity", 0) <= 10:
        return None

    func_name = getattr(block, "name", "")
    classname = getattr(block, "classname", None)
    if classname:
        func_name = f"{classname}.{func_name}"

    if not func_name:
        return None

    relpath = os.path.relpath(filepath, project_root)
    unique_key = f"{relpath}::{func_name}"

    fe = manifest.functions.get(unique_key)
    if not fe or fe.test_count == 0 or fe.semantic_ratio >= 0.5:
        return None

    return LintIssue(
        linter="test_effectiveness",
        kind="TEFF007",
        message=(
            f"'{func_name}' has high complexity ({block.complexity}) "
            f"but low semantic assertion ratio ({fe.semantic_ratio:.1%}). "
            f"Complex code needs strong assertions to catch mutation paths."
        ),
        file=filepath,
        line=getattr(block, "lineno", 0),
        severity="warning",
        confidence=0.7,
        evidence={
            "code": "TEFF007",
            "function": func_name,
            "complexity": block.complexity,
            "semantic_ratio": round(fe.semantic_ratio, 3),
            "test_count": fe.test_count,
        },
        suggestions=[
            "Add equality assertions for each branch path",
            "Consider splitting complex logic into smaller testable functions",
        ],
    )


def _teff007_complex_weak_tests(
    manifest: TestEffectivenessManifest,
    project_root: str,
    py_files: list[str],
) -> list[LintIssue]:
    """TEFF007 — Complex function with weak tests (cross-channel with radon)."""
    try:
        from radon.complexity import cc_visit
    except ImportError:
        return []

    findings: list[LintIssue] = []
    count = 0

    for filepath in py_files:
        try:
            with open(filepath, encoding="utf-8") as f:
                source = f.read()
            blocks = cc_visit(source)
        except (OSError, SyntaxError):
            continue

        for block in blocks:
            finding = _analyze_complexity_block(block, manifest, filepath, project_root)
            if finding:
                count += 1
                if count <= _TOP_N_FINDINGS:
                    findings.append(finding)

    return findings


# ── Channel ──────────────────────────────────────────────────────────


class TestEffectivenessChannel:
    """Supervision channel for test assertion quality.

    Measures test *effectiveness* (assertion quality) as distinct from
    test *coverage* (line execution). Advisory only — findings are
    informational or warning severity.
    """

    name = "test_effectiveness"
    timeout_ms = 12000
    blocking_capable = False

    def should_run(self, event: SupervisionEvent, config: ControlPlaneConfig) -> bool:
        """Run when the project has Python files."""
        return bool(event.project_root)

    def execute(
        self, event: SupervisionEvent, config: ControlPlaneConfig
    ) -> ChannelResult:
        """Execute test effectiveness analysis."""
        start = time.perf_counter()

        project_root = event.project_root
        py_files = _discover_python_files(project_root)
        all_test_files = _discover_test_files(project_root)

        if not py_files or not all_test_files:
            return ChannelResult(
                channel=self.name,
                status="skip",
                severity="none",
                metrics={"reason": "no_python_or_test_files"},
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        # Sample test files if codebase is large (#203)
        test_files, was_sampled = _select_test_files_for_analysis(
            all_test_files, project_root
        )

        # Reuse manifest from prepass if available (avoid duplicate work)
        manifest = event.context.get("test_effectiveness_manifest")
        if manifest is None:
            manifest = build_test_effectiveness_manifest(project_root, py_files, test_files)

        if not manifest.functions:
            return ChannelResult(
                channel=self.name,
                status="skip",
                severity="none",
                metrics={"reason": "no_functions_analyzed"},
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        # Collect findings
        findings: list[LintIssue] = []
        findings.extend(_teff001_low_semantic_ratio(manifest, project_root))
        findings.extend(_teff002_untested_functions(manifest, project_root))
        findings.extend(_teff003_structural_only(manifest, project_root))
        findings.extend(_teff004_high_vulnerability(manifest, project_root))
        findings.extend(_teff005_pure_weak_tests(manifest, project_root, py_files))
        # TEFF006 skipped in v1 — requires coverage.py JSON, deferred
        findings.extend(_teff007_complex_weak_tests(manifest, project_root, py_files))

        elapsed_ms = (time.perf_counter() - start) * 1000

        # Compute project-wide semantic ratio
        total_a = sum(len(fe.assertions) for fe in manifest.functions.values())
        semantic_a = sum(
            sum(1 for a in fe.assertions if a.strength >= SEMANTIC_STRENGTH_THRESHOLD)
            for fe in manifest.functions.values()
        )
        semantic_ratio = semantic_a / max(total_a, 1)

        metrics: dict[str, Any] = {
            "project_effectiveness_score": round(manifest.project_score, 3),
            "semantic_ratio": round(semantic_ratio, 3),
            "functions_analyzed": manifest.functions_analyzed,
            "mutation_vulnerable_count": manifest.mutation_vulnerable_count,
        }

        if was_sampled:
            metrics["sampled"] = True
            metrics["sample_size"] = len(test_files)
            metrics["total_test_files"] = len(all_test_files)
            coverage_pct = round(len(test_files) / len(all_test_files) * 100, 1)
            metrics["sample_coverage"] = (
                f"{len(test_files)}/{len(all_test_files)} test files analyzed ({coverage_pct}%)"
            )

        # Emit telemetry
        _emit_telemetry(
            manifest, project_root, findings, elapsed_ms, len(py_files), len(test_files)
        )

        status: Literal["pass", "fail"] = "fail" if findings else "pass"
        severity: Literal["blocking", "warning", "informational", "none"] = "none"
        if findings:
            severity = "informational"
            if any(f.severity == "warning" for f in findings):
                severity = "warning"

        return ChannelResult(
            channel=self.name,
            status=status,
            severity=severity,
            findings=findings,
            metrics=metrics,
            duration_ms=elapsed_ms,
        )


def _emit_telemetry(
    manifest: TestEffectivenessManifest,
    project_root: str,
    findings: list[LintIssue],
    elapsed_ms: float,
    source_count: int,
    test_count: int,
) -> None:
    """Emit a test_effectiveness_analysis telemetry event."""
    from lintgate.state import log_metric

    log_metric(
        {
            "event": "test_effectiveness_analysis",
            "project": project_root,
            "project_score": round(manifest.project_score, 3),
            "functions_analyzed": manifest.functions_analyzed,
            "mutation_vulnerable_count": manifest.mutation_vulnerable_count,
            "findings_count": len(findings),
            "warning_count": sum(1 for f in findings if f.severity == "warning"),
            "duration_ms": elapsed_ms,
            "source_files": source_count,
            "test_files": test_count,
        }
    )

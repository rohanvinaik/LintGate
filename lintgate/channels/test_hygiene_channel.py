"""Test hygiene channel — stub/duplicate/weak-only test detection for ControlPlane.

Co-equal mesh participant that detects test suite waste:
- THYGIENE001: Stub test body (pass/ellipsis/NotImplementedError)
- THYGIENE002: Weak-only assertions (callable, is_not_none, isinstance only)
- THYGIENE003: Duplicate test function (byte-identical or AST-normalized equivalent)

Safe deletion proposals for byte-identical duplicates and fully subsumed files.
"""

from __future__ import annotations

import ast
import hashlib
import os
import time
from collections import defaultdict
from typing import Any, Literal

from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    RepairAction,
    SupervisionEvent,
)
from lintgate.types import LintIssue

_TOP_N_FINDINGS = 5


# ── Test file discovery ──────────────────────────────────────────────


def _discover_test_files(project_root: str) -> list[str]:
    """Discover test files."""
    from lintgate.linters.test_effectiveness.test_analyzer import (
        _discover_test_files as discover,
    )

    return discover(project_root)


# ── AST helpers ──────────────────────────────────────────────────────


def _parse_file(filepath: str) -> ast.Module | None:
    """Parse a Python file, returning None on failure."""
    try:
        with open(filepath, encoding="utf-8") as f:
            return ast.parse(f.read(), filename=filepath)
    except (OSError, SyntaxError):
        return None


def _read_source(filepath: str) -> str | None:
    """Read file source, returning None on failure."""
    try:
        with open(filepath, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _extract_test_functions(
    tree: ast.Module,
) -> list[tuple[str, ast.FunctionDef, str | None]]:
    """Extract (name, node, class_name) for all test functions/methods."""
    results: list[tuple[str, ast.FunctionDef, str | None]] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                results.append((node.name, node, None))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name.startswith("test_"):
                        results.append((item.name, item, node.name))
    return results


def _function_body_source(source: str, node: ast.FunctionDef) -> str:
    """Extract the body source of a function (excluding the def line and docstring)."""
    body = node.body
    if not body:
        return ""
    # Skip docstring if present
    start_idx = 0
    if (
        isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        start_idx = 1
    if start_idx >= len(body):
        return ""
    first = body[start_idx]
    last = body[-1]
    lines = source.splitlines()
    start_line = first.lineno - 1
    end_line = getattr(last, "end_lineno", last.lineno)
    return "\n".join(lines[start_line:end_line])


def _function_context_hash(node: ast.FunctionDef) -> str:
    """Hash decorators + parameter names to distinguish context-different tests.

    Two tests with the same body but different decorators (e.g. parametrize)
    or different fixture parameters are semantically different.
    """
    parts: list[str] = []
    # Decorators
    for dec in node.decorator_list:
        parts.append(ast.dump(dec, annotate_fields=False))
    # Parameter names (fixture injection)
    for arg in node.args.args:
        if arg.arg != "self":
            parts.append(arg.arg)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _function_body_ast_hash(node: ast.FunctionDef) -> str:
    """Hash the AST-normalized body (strip docstrings, comments, whitespace)."""
    body = list(node.body)
    # Strip leading docstring
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if not body:
        return hashlib.sha256(b"empty").hexdigest()[:16]
    # Create a minimal module with just the body statements
    wrapper = ast.Module(body=body, type_ignores=[])
    dumped = ast.dump(wrapper, annotate_fields=False)
    return hashlib.sha256(dumped.encode()).hexdigest()[:16]


# ── Finding generators ───────────────────────────────────────────────


def _is_stub_body(node: ast.FunctionDef) -> str | None:
    """Check if a function body is a stub. Returns stub type or None."""
    body = node.body
    # Skip docstring
    effective = body
    if (
        effective
        and isinstance(effective[0], ast.Expr)
        and isinstance(effective[0].value, ast.Constant)
        and isinstance(effective[0].value.value, str)
    ):
        effective = effective[1:]

    if not effective:
        return "empty"

    if len(effective) != 1:
        return None

    stmt = effective[0]
    # pass
    if isinstance(stmt, ast.Pass):
        return "pass"
    # ... (Ellipsis)
    if (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is ...
    ):
        return "ellipsis"
    # raise NotImplementedError
    if isinstance(stmt, ast.Raise) and stmt.exc is not None:
        if isinstance(stmt.exc, ast.Call):
            func = stmt.exc.func
            if isinstance(func, ast.Name) and func.id == "NotImplementedError":
                return "not_implemented"
        elif isinstance(stmt.exc, ast.Name) and stmt.exc.id == "NotImplementedError":
            return "not_implemented"

    return None


def _thygiene001_stub_tests(
    test_files: list[str],
) -> list[LintIssue]:
    """THYGIENE001 — Stub test body (pass/ellipsis/NotImplementedError)."""
    findings: list[LintIssue] = []

    for filepath in test_files:
        tree = _parse_file(filepath)
        if tree is None:
            continue

        for name, node, class_name in _extract_test_functions(tree):
            stub_type = _is_stub_body(node)
            if stub_type is None:
                continue

            display_name = f"{class_name}.{name}" if class_name else name
            findings.append(
                LintIssue(
                    linter="test_hygiene",
                    kind="THYGIENE001",
                    message=(
                        f"'{display_name}' has a stub body ({stub_type}). "
                        f"This test contributes zero specification value."
                    ),
                    file=filepath,
                    line=node.lineno,
                    severity="warning",
                    confidence=0.95,
                    evidence={
                        "code": "THYGIENE001",
                        "function": display_name,
                        "body_type": stub_type,
                    },
                )
            )

            if len(findings) >= _TOP_N_FINDINGS * 2:
                break

    return findings


def _thygiene002_weak_only(
    test_files: list[str],
) -> list[LintIssue]:
    """THYGIENE002 — Tests whose only assertions are weak existence/type checks."""
    from lintgate.linters.test_effectiveness.assertion_classifier import (
        classify_test_file_from_path,
    )
    from lintgate.linters.test_effectiveness.types import AssertionKind

    WEAK_ONLY_KINDS = {
        AssertionKind.IS_NONE,
        AssertionKind.IS_NOT_NONE,
        AssertionKind.IS_TRUE,
        AssertionKind.IS_FALSE,
        AssertionKind.ISINSTANCE_CHECK,
        AssertionKind.HASATTR_CHECK,
    }

    findings: list[LintIssue] = []

    for filepath in test_files:
        try:
            classifications = classify_test_file_from_path(filepath)
        except Exception:
            continue

        if not classifications:
            continue

        for func_name, assertions in classifications.items():
            if not assertions:
                continue  # No assertions = possibly THYGIENE001
            kinds = {a.kind for a in assertions}
            if kinds <= WEAK_ONLY_KINDS:
                kind_names = sorted(k.value for k in kinds)
                findings.append(
                    LintIssue(
                        linter="test_hygiene",
                        kind="THYGIENE002",
                        message=(
                            f"'{func_name}' has {len(assertions)} assertion(s) "
                            f"but all are weak ({', '.join(kind_names)}). "
                            f"These don't verify computed values."
                        ),
                        file=filepath,
                        severity="informational",
                        confidence=0.85,
                        evidence={
                            "code": "THYGIENE002",
                            "function": func_name,
                            "assertion_kinds": kind_names,
                            "assertion_count": len(assertions),
                        },
                    )
                )

                if len(findings) >= _TOP_N_FINDINGS * 2:
                    return findings

    return findings


# ── Duplicate detection ──────────────────────────────────────────────


def _build_test_fingerprints(
    test_files: list[str],
) -> list[dict[str, Any]]:
    """Build fingerprints for all test functions across all files.

    Returns list of dicts with keys:
        file, name, class_name, line, body_hash, ast_hash
    """
    fingerprints: list[dict[str, Any]] = []

    for filepath in test_files:
        source = _read_source(filepath)
        if source is None:
            continue
        tree = _parse_file(filepath)
        if tree is None:
            continue

        for name, node, class_name in _extract_test_functions(tree):
            body_src = _function_body_source(source, node)
            body_hash = hashlib.sha256(body_src.encode()).hexdigest()[:16]
            ast_hash = _function_body_ast_hash(node)
            ctx_hash = _function_context_hash(node)

            fingerprints.append(
                {
                    "file": filepath,
                    "name": name,
                    "class_name": class_name,
                    "line": node.lineno,
                    "body_hash": body_hash,
                    "ast_hash": ast_hash,
                    "ctx_hash": ctx_hash,
                    "body_source": body_src,
                }
            )

    return fingerprints


def _thygiene003_duplicates(
    test_files: list[str],
    project_root: str,
) -> tuple[list[LintIssue], list[RepairAction]]:
    """THYGIENE003 — Duplicate test functions.

    Returns (findings, repair_actions).
    """
    fingerprints = _build_test_fingerprints(test_files)
    findings: list[LintIssue] = []
    repairs: list[RepairAction] = []

    # Group by name + body_hash + ctx_hash (byte-identical across files,
    # same decorators and fixture params)
    by_name_body: dict[str, list[dict]] = defaultdict(list)
    for fp in fingerprints:
        key = f"{fp['name']}:{fp['body_hash']}:{fp['ctx_hash']}"
        by_name_body[key].append(fp)

    seen_dupes: set[str] = set()
    for key, group in by_name_body.items():
        if len(group) < 2:
            continue
        # Only flag cross-file duplicates
        files = {fp["file"] for fp in group}
        if len(files) < 2:
            continue

        # Sort by file path — first occurrence is the "keeper"
        sorted_group = sorted(group, key=lambda fp: fp["file"])
        keeper = sorted_group[0]
        keeper_rel = os.path.relpath(keeper["file"], project_root)

        for dup in sorted_group[1:]:
            dup_rel = os.path.relpath(dup["file"], project_root)
            dup_key = f"{dup['file']}:{dup['name']}"
            if dup_key in seen_dupes:
                continue
            seen_dupes.add(dup_key)

            display = f"{dup['class_name']}.{dup['name']}" if dup["class_name"] else dup["name"]
            findings.append(
                LintIssue(
                    linter="test_hygiene",
                    kind="THYGIENE003",
                    message=(
                        f"'{display}' in {dup_rel} is byte-identical to "
                        f"{keeper_rel}:{keeper['name']}. Safe to remove."
                    ),
                    file=dup["file"],
                    line=dup["line"],
                    severity="warning",
                    confidence=0.95,
                    evidence={
                        "code": "THYGIENE003",
                        "function": dup["name"],
                        "duplicate_type": "byte_identical",
                        "keeper_file": keeper_rel,
                        "keeper_function": keeper["name"],
                        "body_hash": dup["body_hash"],
                    },
                )
            )

    # Group by name + ast_hash + ctx_hash (AST-equivalent but not byte-identical,
    # same decorators and fixture params)
    by_name_ast: dict[str, list[dict]] = defaultdict(list)
    for fp in fingerprints:
        key = f"{fp['name']}:{fp['ast_hash']}:{fp['ctx_hash']}"
        by_name_ast[key].append(fp)

    for key, group in by_name_ast.items():
        if len(group) < 2:
            continue
        files = {fp["file"] for fp in group}
        if len(files) < 2:
            continue

        sorted_group = sorted(group, key=lambda fp: fp["file"])
        keeper = sorted_group[0]
        keeper_rel = os.path.relpath(keeper["file"], project_root)

        for dup in sorted_group[1:]:
            dup_key = f"{dup['file']}:{dup['name']}"
            if dup_key in seen_dupes:
                continue  # Already caught by byte-identical
            seen_dupes.add(dup_key)

            dup_rel = os.path.relpath(dup["file"], project_root)
            display = f"{dup['class_name']}.{dup['name']}" if dup["class_name"] else dup["name"]
            findings.append(
                LintIssue(
                    linter="test_hygiene",
                    kind="THYGIENE003",
                    message=(
                        f"'{display}' in {dup_rel} is AST-equivalent to "
                        f"{keeper_rel}:{keeper['name']}. Review before removing."
                    ),
                    file=dup["file"],
                    line=dup["line"],
                    severity="informational",
                    confidence=0.75,
                    evidence={
                        "code": "THYGIENE003",
                        "function": dup["name"],
                        "duplicate_type": "ast_equivalent",
                        "keeper_file": keeper_rel,
                        "keeper_function": keeper["name"],
                        "ast_hash": dup["ast_hash"],
                    },
                )
            )

    # Check for fully subsumed files (THYGIENE005)
    _add_subsumption_findings(fingerprints, test_files, project_root, findings, repairs)

    return findings[: _TOP_N_FINDINGS * 3], repairs


def _add_subsumption_findings(
    fingerprints: list[dict],
    test_files: list[str],
    project_root: str,
    findings: list[LintIssue],
    repairs: list[RepairAction],
) -> None:
    """Detect files that are fully subsumed by another file."""
    # Build per-file fingerprint sets (name + body + context)
    file_hashes: dict[str, set[str]] = defaultdict(set)
    file_test_count: dict[str, int] = defaultdict(int)
    for fp in fingerprints:
        file_hashes[fp["file"]].add(f"{fp['name']}:{fp['body_hash']}:{fp['ctx_hash']}")
        file_test_count[fp["file"]] += 1

    for filepath in test_files:
        if filepath not in file_hashes or file_test_count[filepath] == 0:
            continue
        my_hashes = file_hashes[filepath]

        for other_file in test_files:
            if other_file == filepath:
                continue
            if other_file not in file_hashes:
                continue
            if file_test_count[other_file] <= file_test_count[filepath]:
                continue  # Only check if other has MORE tests
            other_hashes = file_hashes[other_file]
            if my_hashes <= other_hashes:
                # All tests in filepath exist in other_file
                rel_path = os.path.relpath(filepath, project_root)
                other_rel = os.path.relpath(other_file, project_root)
                findings.append(
                    LintIssue(
                        linter="test_hygiene",
                        kind="THYGIENE005",
                        message=(
                            f"All {file_test_count[filepath]} tests in {rel_path} "
                            f"are byte-identical duplicates of tests in {other_rel}. "
                            f"Safe to delete entire file."
                        ),
                        file=filepath,
                        severity="warning",
                        confidence=0.95,
                        evidence={
                            "code": "THYGIENE005",
                            "subsumed_file": rel_path,
                            "superset_file": other_rel,
                            "test_count": file_test_count[filepath],
                        },
                    )
                )
                repairs.append(
                    RepairAction(
                        channel="test_hygiene",
                        kind="safe_delete",
                        summary=f"Delete {rel_path} (fully subsumed by {other_rel})",
                        payload={
                            "action": "delete_file",
                            "target_path": filepath,
                            "reason": f"All {file_test_count[filepath]} tests are byte-identical duplicates of {other_rel}",
                        },
                        safe=True,
                    )
                )
                break  # Only need one superset


# ── Channel ──────────────────────────────────────────────────────────


class TestHygieneChannel:
    """Supervision channel for test suite hygiene.

    Detects stub tests, weak-only assertions, and duplicate tests.
    Advisory only — findings are warning or informational severity.
    """

    name = "test_hygiene"
    timeout_ms = 10000
    blocking_capable = False

    def should_run(self, event: SupervisionEvent, config: ControlPlaneConfig) -> bool:
        """Run when the project has a root path."""
        return bool(event.project_root)

    def execute(self, event: SupervisionEvent, config: ControlPlaneConfig) -> ChannelResult:
        """Execute test hygiene analysis."""
        start = time.perf_counter()
        project_root = event.project_root

        test_files = _discover_test_files(project_root)
        if not test_files:
            return ChannelResult(
                channel=self.name,
                status="skip",
                severity="none",
                metrics={"reason": "no_test_files"},
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        # Optional file filter from settings
        file_filter = config.channels.get(self.name, None) and config.channels[
            self.name
        ].settings.get("file_filter")
        if file_filter:
            test_files = [f for f in test_files if file_filter in f]

        findings: list[LintIssue] = []
        repairs: list[RepairAction] = []

        # THYGIENE001: Stub tests
        findings.extend(_thygiene001_stub_tests(test_files))

        # THYGIENE002: Weak-only assertions
        findings.extend(_thygiene002_weak_only(test_files))

        # THYGIENE003 + THYGIENE005: Duplicates and subsumption
        dup_findings, dup_repairs = _thygiene003_duplicates(test_files, project_root)
        findings.extend(dup_findings)
        repairs.extend(dup_repairs)

        elapsed_ms = (time.perf_counter() - start) * 1000

        # Metrics
        stub_count = sum(1 for f in findings if f.kind == "THYGIENE001")
        weak_count = sum(1 for f in findings if f.kind == "THYGIENE002")
        dup_count = sum(1 for f in findings if f.kind == "THYGIENE003")
        subsumed_count = sum(1 for f in findings if f.kind == "THYGIENE005")

        metrics: dict[str, Any] = {
            "test_files_scanned": len(test_files),
            "stub_tests": stub_count,
            "weak_only_tests": weak_count,
            "duplicate_tests": dup_count,
            "subsumed_files": subsumed_count,
            "total_findings": len(findings),
            "safe_delete_proposals": len(repairs),
        }

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
            repairs=repairs,
            metrics=metrics,
            duration_ms=elapsed_ms,
        )

"""Finding generators for test hygiene (THYGIENE001, THYGIENE002).

Extracted from test_hygiene_channel.py to keep the main module under 400 lines.
"""

from __future__ import annotations

import ast

from lintgate.types import LintIssue

from ._test_hygiene_ast import _extract_test_functions, _parse_file

_TOP_N_FINDINGS = 5


def _is_stub_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
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
    """THYGIENE001 -- Stub test body (pass/ellipsis/NotImplementedError)."""
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
    """THYGIENE002 -- Tests whose only assertions are weak existence/type checks."""
    from lintgate.linters.test_effectiveness.assertion_classifier import (
        classify_test_file_from_path,
    )
    from lintgate.linters.test_effectiveness.types import AssertionKind

    weak_only_kinds = {
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
            if kinds <= weak_only_kinds:
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

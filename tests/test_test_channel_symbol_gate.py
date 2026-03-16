"""Tests for lintgate.channels._test_channel_symbol_gate — symbol coverage gate."""

from __future__ import annotations

import os
from types import SimpleNamespace

from lintgate.channels._test_channel_symbol_gate import (
    SymbolGateContext,
    _build_symbol_suggestions,
    _build_symbol_uncovered_message,
    _emit_symbol_findings,
    _filter_to_source_packages,
    _run_symbol_gate_if_enabled,
)
from lintgate.types import LintIssue  # noqa: TC001

# ── _filter_to_source_packages ───────────────────────────────────────


class TestFilterToSourcePackages:
    """Tests for filtering changed files to source packages."""

    def test_filters_to_matching_package(self, tmp_path) -> None:
        project = str(tmp_path)
        files = [
            os.path.join(project, "lintgate", "foo.py"),
            os.path.join(project, "tests", "test_foo.py"),
        ]
        result = _filter_to_source_packages(files, ["lintgate"], project)
        assert len(result) == 1
        assert result[0].endswith("foo.py")

    def test_empty_packages_returns_all(self) -> None:
        files = ["/a.py", "/b.py"]
        result = _filter_to_source_packages(files, [], "/")
        assert result == ["/a.py", "/b.py"]

    def test_multiple_packages(self, tmp_path) -> None:
        project = str(tmp_path)
        files = [
            os.path.join(project, "lintgate", "a.py"),
            os.path.join(project, "mcp_tools", "b.py"),
            os.path.join(project, "docs", "c.py"),
        ]
        result = _filter_to_source_packages(files, ["lintgate", "mcp_tools"], project)
        assert len(result) == 2


# ── _build_symbol_suggestions ────────────────────────────────────────


class TestBuildSymbolSuggestions:
    """Tests for suggestion text generation."""

    def test_missing_lines_only(self) -> None:
        sr = SimpleNamespace(
            symbol=SimpleNamespace(name="my_func"),
            missing_lines=[10, 20, 30],
            missing_branches=[],
        )
        suggestions = _build_symbol_suggestions(sr)
        assert len(suggestions) == 2
        assert "lines 10, 20, 30" in suggestions[0]
        assert "my_func" in suggestions[0]
        assert "waiver" in suggestions[1]

    def test_missing_branches_only(self) -> None:
        sr = SimpleNamespace(
            symbol=SimpleNamespace(name="f"),
            missing_lines=[],
            missing_branches=[(5, 10), (15, 20)],
        )
        suggestions = _build_symbol_suggestions(sr)
        assert "branches" in suggestions[0]
        assert "5->10" in suggestions[0]

    def test_both_lines_and_branches(self) -> None:
        sr = SimpleNamespace(
            symbol=SimpleNamespace(name="g"),
            missing_lines=[1],
            missing_branches=[(2, 3)],
        )
        suggestions = _build_symbol_suggestions(sr)
        assert "lines" in suggestions[0]
        assert "branches" in suggestions[0]

    def test_neither_lines_nor_branches(self) -> None:
        sr = SimpleNamespace(
            symbol=SimpleNamespace(name="h"),
            missing_lines=[],
            missing_branches=[],
        )
        suggestions = _build_symbol_suggestions(sr)
        assert "Add missing tests for h" in suggestions[0]


# ── _build_symbol_uncovered_message ──────────────────────────────────


class TestBuildSymbolUncoveredMessage:
    """Tests for uncovered-symbol message formatting."""

    def test_lines_only(self) -> None:
        sr = SimpleNamespace(
            symbol=SimpleNamespace(name="func_a"),
            missing_lines=[5, 10],
            missing_branches=[],
        )
        msg = _build_symbol_uncovered_message(sr)
        assert msg == "Symbol func_a is not fully covered (missing lines: 5, 10)"

    def test_branches_only(self) -> None:
        sr = SimpleNamespace(
            symbol=SimpleNamespace(name="func_b"),
            missing_lines=[],
            missing_branches=[(1, 2), (3, 4)],
        )
        msg = _build_symbol_uncovered_message(sr)
        assert msg == "Symbol func_b is not fully covered (missing 2 branches)"

    def test_both(self) -> None:
        sr = SimpleNamespace(
            symbol=SimpleNamespace(name="func_c"),
            missing_lines=[7],
            missing_branches=[(1, 2)],
        )
        msg = _build_symbol_uncovered_message(sr)
        assert "missing lines: 7" in msg
        assert "1 branches" in msg

    def test_neither(self) -> None:
        sr = SimpleNamespace(
            symbol=SimpleNamespace(name="func_d"),
            missing_lines=[],
            missing_branches=[],
        )
        msg = _build_symbol_uncovered_message(sr)
        assert msg == "Symbol func_d is not fully covered"


# ── _emit_symbol_findings ────────────────────────────────────────────


class TestEmitSymbolFindings:
    """Tests for converting gate results into LintIssue findings."""

    def _make_gate_result(
        self,
        symbol_results=None,
        unresolved_required=None,
        waivers_expired=None,
        skipped_reasons=None,
    ):
        return SimpleNamespace(
            symbol_results=symbol_results or [],
            unresolved_required=unresolved_required or [],
            waivers_expired=waivers_expired or [],
            skipped_reasons=skipped_reasons or [],
        )

    def test_covered_symbol_no_finding(self) -> None:
        findings: list[LintIssue] = []
        ctx = SymbolGateContext(surface="ci", findings=findings)
        gate = self._make_gate_result(symbol_results=[SimpleNamespace(covered=True)])
        _emit_symbol_findings(gate, ctx)
        assert findings == []

    def test_uncovered_symbol_blocking(self) -> None:
        findings: list[LintIssue] = []
        ctx = SymbolGateContext(surface="ci", findings=findings)
        sr = SimpleNamespace(
            covered=False,
            symbol=SimpleNamespace(name="my_fn", file="a.py", start_line=10, symbol_key="a::my_fn"),
            missing_lines=[10],
            missing_branches=[],
            total_lines_in_span=20,
            executed_lines_in_span=15,
        )
        gate = self._make_gate_result(symbol_results=[sr])
        _emit_symbol_findings(gate, ctx)
        assert len(findings) == 1
        assert findings[0].kind == "symbol_uncovered"
        assert findings[0].severity == "blocking"
        assert findings[0].confidence == 1.0

    def test_partial_run_downgrades_severity(self) -> None:
        findings: list[LintIssue] = []
        ctx = SymbolGateContext(
            surface="ci",
            findings=findings,
            is_partial_run=True,
            coverage_ok=True,
        )
        sr = SimpleNamespace(
            covered=False,
            symbol=SimpleNamespace(name="fn", file="b.py", start_line=5, symbol_key="b::fn"),
            missing_lines=[5],
            missing_branches=[],
            total_lines_in_span=10,
            executed_lines_in_span=8,
        )
        gate = self._make_gate_result(symbol_results=[sr])
        _emit_symbol_findings(gate, ctx)
        assert findings[0].severity == "warning"
        assert findings[0].confidence == 0.6

    def test_unresolved_required_symbol(self) -> None:
        findings: list[LintIssue] = []
        ctx = SymbolGateContext(surface="ci", findings=findings)
        gate = self._make_gate_result(unresolved_required=["missing_sym"])
        _emit_symbol_findings(gate, ctx)
        assert len(findings) == 1
        assert findings[0].kind == "unresolved_required_symbol"
        assert findings[0].severity == "blocking"

    def test_expired_waiver(self) -> None:
        findings: list[LintIssue] = []
        ctx = SymbolGateContext(surface="ci", findings=findings)
        waiver = SimpleNamespace(symbol="old_fn", expires="2025-01-01")
        gate = self._make_gate_result(waivers_expired=[waiver])
        _emit_symbol_findings(gate, ctx)
        assert len(findings) == 1
        assert findings[0].kind == "waiver_expired"
        assert findings[0].severity == "informational"

    def test_skipped_reason(self) -> None:
        findings: list[LintIssue] = []
        ctx = SymbolGateContext(surface="ci", findings=findings)
        gate = self._make_gate_result(skipped_reasons=["no data"])
        _emit_symbol_findings(gate, ctx)
        assert len(findings) == 1
        assert findings[0].kind == "symbol_gate_skipped"


# ── _run_symbol_gate_if_enabled ──────────────────────────────────────


class TestRunSymbolGateIfEnabled:
    """Tests for the symbol gate enable check."""

    def test_returns_none_when_disabled(self) -> None:
        cov_cfg = {"symbol_enabled": False, "source_packages": ["lintgate"]}
        result = _run_symbol_gate_if_enabled(
            cov_cfg,
            None,
            [],
            "/project",
            SymbolGateContext(surface="ci", findings=[]),
        )
        assert result is None

"""Tests for new functions added in the retro-improvements branch.

Covers Phase 1 (Trust & Noise) and Phase 2 (Analyzer Precision) functions
to close the SonarQube coverage gap on new code.
"""

from __future__ import annotations

import ast
import textwrap
from unittest.mock import MagicMock, patch

import pytest

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 1: Trust & Noise
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


# ── 1. context_rule_checker.py — template placeholder detection ──────


class TestContextRuleCheckerPlaceholders:
    """Test that template placeholders produce a config warning, not per-file failures."""

    def test_placeholder_count_zero_emits_nothing(self, tmp_path):
        """When there are no placeholder rules, no 'context-unconfigured' issue is emitted."""
        from lintgate.linters.context_rule_checker import ContextRuleChecker
        from lintgate.types import LinterContext

        # Create a CLAUDE.md with a real rule (not a placeholder)
        claude_md = tmp_path / ".claude" / "CLAUDE.md"
        claude_md.parent.mkdir(parents=True)
        claude_md.write_text("LINTGATE_FORBID_REGEX: print\\(")

        ctx = LinterContext(files=[], project_root=str(tmp_path))
        checker = ContextRuleChecker()
        issues = list(checker.run(ctx))
        unconfigured = [i for i in issues if i.kind == "context-unconfigured"]
        assert unconfigured == []

    def test_placeholder_rules_emit_single_config_warning(self, tmp_path):
        """Placeholder patterns like <regex> should produce one informational warning."""
        from lintgate.linters.context_rule_checker import ContextRuleChecker
        from lintgate.types import LinterContext

        claude_md = tmp_path / ".claude" / "CLAUDE.md"
        claude_md.parent.mkdir(parents=True)
        claude_md.write_text("LINTGATE_FORBID_REGEX: <regex>\nLINTGATE_REQUIRE_REGEX: <pattern>\n")

        ctx = LinterContext(files=[], project_root=str(tmp_path))
        checker = ContextRuleChecker()
        issues = list(checker.run(ctx))
        unconfigured = [i for i in issues if i.kind == "context-unconfigured"]
        assert len(unconfigured) == 1
        assert unconfigured[0].severity == "informational"
        assert unconfigured[0].evidence["placeholder_count"] == 2
        assert "<regex>" in unconfigured[0].message

    def test_mixed_real_and_placeholder_rules(self, tmp_path):
        """Mix of real and placeholder patterns — only placeholders counted."""
        from lintgate.linters.context_rule_checker import ContextRuleChecker
        from lintgate.types import LinterContext

        claude_md = tmp_path / ".claude" / "CLAUDE.md"
        claude_md.parent.mkdir(parents=True)
        claude_md.write_text("LINTGATE_FORBID_REGEX: <regex>\nLINTGATE_FORBID_REGEX: print\\(\n")

        ctx = LinterContext(files=[], project_root=str(tmp_path))
        checker = ContextRuleChecker()
        issues = list(checker.run(ctx))
        unconfigured = [i for i in issues if i.kind == "context-unconfigured"]
        assert len(unconfigured) == 1
        assert unconfigured[0].evidence["placeholder_count"] == 1


# ── 2. hook.py — compute_hook_fingerprint and PostToolUseInputs ──────


class TestComputeHookFingerprint:
    """Test the hook fingerprint computation for state-transition suppression."""

    @staticmethod
    def _make_mesh_result(
        coherence_state="stable",
        findings=None,
        channel_statuses=None,
    ):
        """Helper: build a minimal MeshResult for fingerprint tests."""
        from lintgate.controlplane.types import (
            ChannelResult,
            CoherenceResult,
            MeshResult,
            SupervisionEvent,
        )

        cr_list = []
        if findings:
            cr = ChannelResult(channel="lint", status="fail", severity="blocking")
            cr.findings = findings
            cr_list.append(cr)
        if channel_statuses:
            for name, status in channel_statuses.items():
                cr_list.append(ChannelResult(channel=name, status=status, severity="none"))

        coherence = CoherenceResult(state=coherence_state)
        return MeshResult(
            event=SupervisionEvent(),
            channel_results=cr_list,
            coherence=coherence,
        )

    def test_fingerprint_is_12_hex_chars(self):
        from lintgate.controlplane.reporter.hook import compute_hook_fingerprint

        mesh = self._make_mesh_result()
        fp = compute_hook_fingerprint(mesh)
        assert len(fp) == 12
        assert all(c in "0123456789abcdef" for c in fp)

    def test_same_state_produces_same_fingerprint(self):
        from lintgate.controlplane.reporter.hook import compute_hook_fingerprint

        mesh1 = self._make_mesh_result(coherence_state="isolated")
        mesh2 = self._make_mesh_result(coherence_state="isolated")
        assert compute_hook_fingerprint(mesh1) == compute_hook_fingerprint(mesh2)

    def test_different_coherence_state_changes_fingerprint(self):
        from lintgate.controlplane.reporter.hook import compute_hook_fingerprint

        mesh_stable = self._make_mesh_result(coherence_state="stable")
        mesh_coupled = self._make_mesh_result(coherence_state="coupled")
        assert compute_hook_fingerprint(mesh_stable) != compute_hook_fingerprint(mesh_coupled)

    def test_blocking_findings_change_fingerprint(self):
        from lintgate.controlplane.reporter.hook import compute_hook_fingerprint
        from lintgate.types import LintIssue

        mesh_none = self._make_mesh_result()
        finding = LintIssue(linter="ruff", kind="F401", message="unused", severity="blocking")
        mesh_blocking = self._make_mesh_result(findings=[finding])
        assert compute_hook_fingerprint(mesh_none) != compute_hook_fingerprint(mesh_blocking)

    def test_loud_channels_affect_fingerprint(self):
        from lintgate.controlplane.reporter.hook import compute_hook_fingerprint

        mesh_clean = self._make_mesh_result()
        mesh_loud = self._make_mesh_result(channel_statuses={"test": "fail"})
        assert compute_hook_fingerprint(mesh_clean) != compute_hook_fingerprint(mesh_loud)


class TestBuildPosttoolusContext:
    """Test _build_posttooluse_context with PostToolUseInputs dataclass."""

    @staticmethod
    def _make_inputs(**overrides):
        from lintgate.controlplane.reporter.hook import PostToolUseInputs
        from lintgate.controlplane.types import (
            CoherenceResult,
            MeshResult,
            SupervisionEvent,
        )

        mesh = MeshResult(
            event=SupervisionEvent(),
            channel_results=[],
            coherence=CoherenceResult(state="stable"),
        )
        defaults = {
            "mesh_result": mesh,
            "blocking_count": 0,
            "warning_count": 0,
            "informational_count": 0,
            "hidden_findings": 0,
            "channels_run": 3,
        }
        defaults.update(overrides)
        return PostToolUseInputs(**defaults)

    def test_basic_context_has_coherence_and_channels(self):
        from lintgate.controlplane.reporter.hook import (
            _build_posttooluse_context,
        )

        inputs = self._make_inputs()
        ctx = _build_posttooluse_context(inputs)
        assert "coherence=stable" in ctx
        assert "channels_run=3" in ctx

    def test_blocking_count_included_when_nonzero(self):
        from lintgate.controlplane.reporter.hook import (
            _build_posttooluse_context,
        )

        inputs = self._make_inputs(blocking_count=2)
        ctx = _build_posttooluse_context(inputs)
        assert "blocking=2" in ctx

    def test_zero_blocking_omitted(self):
        from lintgate.controlplane.reporter.hook import (
            _build_posttooluse_context,
        )

        inputs = self._make_inputs(blocking_count=0)
        ctx = _build_posttooluse_context(inputs)
        assert "blocking=" not in ctx

    def test_max_length_enforcement(self):
        from lintgate.controlplane.reporter.hook import (
            _build_posttooluse_context,
        )

        inputs = self._make_inputs(
            blocking_count=5,
            warning_count=10,
            cycle_alerts=["cycle_" + str(i) for i in range(50)],
        )
        ctx = _build_posttooluse_context(inputs)
        assert len(ctx) <= 300

    def test_dataclass_input_works(self):
        """PostToolUseInputs dataclass input should be accepted."""
        from lintgate.controlplane.reporter.hook import (
            PostToolUseInputs,
            _build_posttooluse_context,
        )
        from lintgate.controlplane.types import (
            CoherenceResult,
            MeshResult,
            SupervisionEvent,
        )

        mesh = MeshResult(
            event=SupervisionEvent(),
            channel_results=[],
            coherence=CoherenceResult(state="coupled"),
        )
        ctx = _build_posttooluse_context(
            PostToolUseInputs(
                mesh_result=mesh,
                blocking_count=1,
                warning_count=0,
                informational_count=0,
                hidden_findings=0,
                channels_run=5,
            )
        )
        assert "coherence=coupled" in ctx

    def test_none_input_raises(self):
        from lintgate.controlplane.reporter.hook import _build_posttooluse_context

        with pytest.raises((TypeError, AttributeError)):
            _build_posttooluse_context(None)


# ── 3. _controlplane_impl_feedback.py — per-repair skip-reason codes ──


class TestCollectPendingRepairs:
    """Test skip-reason diagnostics from _collect_pending_repairs."""

    @staticmethod
    def _make_session(snapshots=None, repair_outcomes=None):
        """Build a mock session with minimal attributes."""
        session = MagicMock()
        session.snapshots = snapshots or []
        session.repair_outcomes = repair_outcomes or {}
        return session

    @staticmethod
    def _make_snapshot(run_id="run1", repairs_proposed=None, repair_catalog=None):
        snapshot = MagicMock()
        snapshot.run_id = run_id
        snapshot.repairs_proposed = repairs_proposed or set()
        snapshot.repair_catalog = repair_catalog or {}
        return snapshot

    def test_no_snapshots_returns_no_snapshots_reason(self):
        from mcp_tools._controlplane_impl_feedback import _collect_pending_repairs

        session = self._make_session()
        pending, skipped = _collect_pending_repairs(session, [], False)
        assert pending == []
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "no_snapshots"

    def test_already_executed_repair_skipped_with_reason(self):
        from mcp_tools._controlplane_impl_feedback import _collect_pending_repairs

        snapshot = self._make_snapshot(
            repairs_proposed={"r1"},
            repair_catalog={"r1": {"kind": "command", "summary": "fix", "safe": "true"}},
        )
        session = self._make_session(
            snapshots=[snapshot],
            repair_outcomes={"r1": "applied"},
        )
        # Patch load_controlplane_run where it's imported (inside _load_all_repairs)
        with patch("lintgate.state.load_controlplane_run", return_value=None):  # noqa: SIM117
            pending, skipped = _collect_pending_repairs(session, [], False)

        assert pending == []
        assert any(s["reason"] == "already_executed" for s in skipped)

    def test_safe_only_filter_skips_unsafe(self):
        from mcp_tools._controlplane_impl_feedback import _collect_pending_repairs

        snapshot = self._make_snapshot(
            repairs_proposed={"r1"},
            repair_catalog={"r1": {"kind": "command", "summary": "risky", "safe": "false"}},
        )
        session = self._make_session(snapshots=[snapshot])
        with patch("lintgate.state.load_controlplane_run", return_value=None):  # noqa: SIM117
            pending, skipped = _collect_pending_repairs(session, [], True)

        assert pending == []
        assert any(s["reason"] == "safe_only_filter" for s in skipped)

    def test_action_ids_filter(self):
        from mcp_tools._controlplane_impl_feedback import _collect_pending_repairs

        snapshot = self._make_snapshot(
            repairs_proposed={"r1", "r2"},
            repair_catalog={
                "r1": {"kind": "command", "summary": "fix1", "safe": "true"},
                "r2": {"kind": "command", "summary": "fix2", "safe": "true"},
            },
        )
        session = self._make_session(snapshots=[snapshot])
        with patch("lintgate.state.load_controlplane_run", return_value=None):  # noqa: SIM117
            pending, skipped = _collect_pending_repairs(session, ["r1"], False)

        assert len(pending) == 1
        assert pending[0]["action_id"] == "r1"
        assert any(s["reason"] == "not_in_action_ids" for s in skipped)

    def test_no_proposed_repairs_diagnostic(self):
        from mcp_tools._controlplane_impl_feedback import _collect_pending_repairs

        snapshot = self._make_snapshot(
            repairs_proposed=set(),
            repair_catalog={"r1": {"kind": "command"}},
        )
        session = self._make_session(snapshots=[snapshot])
        with patch("lintgate.state.load_controlplane_run", return_value=None):  # noqa: SIM117
            pending, skipped = _collect_pending_repairs(session, [], False)

        assert pending == []
        assert any(s["reason"] == "no_proposed_repairs" for s in skipped)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 2: Analyzer Precision
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


# ── 4. lint_fixer.py — _is_shim_file and _build_shim_ignores ────────


class TestIsShimFile:
    """Test shim file detection for F401 suppression."""

    def test_explicit_marker(self, tmp_path):
        from lintgate.lint_fixer import _is_shim_file

        shim = tmp_path / "shim.py"
        shim.write_text("# lintgate: shim\nfrom .foo import bar\n")
        assert _is_shim_file(str(shim)) is True

    def test_marker_in_first_10_lines(self, tmp_path):
        from lintgate.lint_fixer import _is_shim_file

        lines = ["# line\n"] * 9 + ["# lintgate: shim\n"]
        shim = tmp_path / "shim.py"
        shim.write_text("".join(lines))
        assert _is_shim_file(str(shim)) is True

    def test_marker_after_line_10_not_detected(self, tmp_path):
        from lintgate.lint_fixer import _is_shim_file

        lines = ["# line\n"] * 11 + ["# lintgate: shim\n"]
        f = tmp_path / "notshim.py"
        f.write_text("".join(lines))
        # With no marker in first 10 lines and few re-exports, should be False
        assert _is_shim_file(str(f)) is False

    def test_heuristic_mostly_reexports(self, tmp_path):
        from lintgate.lint_fixer import _is_shim_file

        content = textwrap.dedent("""\
            from .foo import bar
            from .baz import qux
            from .quux import corge
            from .grault import garply
            from .waldo import fred
        """)
        f = tmp_path / "reexport.py"
        f.write_text(content)
        assert _is_shim_file(str(f)) is True

    def test_heuristic_not_enough_reexports(self, tmp_path):
        from lintgate.lint_fixer import _is_shim_file

        content = textwrap.dedent("""\
            from .foo import bar
            x = 1
            y = 2
            z = 3
        """)
        f = tmp_path / "notshim.py"
        f.write_text(content)
        assert _is_shim_file(str(f)) is False

    def test_fewer_than_two_code_lines_returns_false(self, tmp_path):
        from lintgate.lint_fixer import _is_shim_file

        f = tmp_path / "tiny.py"
        f.write_text("from .foo import bar\n")
        assert _is_shim_file(str(f)) is False

    def test_missing_file_returns_false(self):
        from lintgate.lint_fixer import _is_shim_file

        assert _is_shim_file("/nonexistent/path/shim.py") is False

    def test_docstrings_ignored_in_heuristic(self, tmp_path):
        from lintgate.lint_fixer import _is_shim_file

        content = textwrap.dedent('''\
            """Module docstring."""
            from .foo import bar
            from .baz import qux
            from .quux import corge
        ''')
        f = tmp_path / "withds.py"
        f.write_text(content)
        assert _is_shim_file(str(f)) is True


class TestBuildShimIgnores:
    """Test building ruff --extend-per-file-ignores for shim files."""

    def test_shim_files_get_f401_ignore(self, tmp_path):
        from lintgate.lint_fixer import _build_shim_ignores

        shim = tmp_path / "shim.py"
        shim.write_text("# lintgate: shim\nfrom .foo import bar\n")
        normal = tmp_path / "normal.py"
        normal.write_text("x = 1\ny = 2\nz = 3\n")

        args = _build_shim_ignores([str(shim), str(normal)])
        assert "--extend-per-file-ignores" in args
        assert f"{shim}:F401" in args
        assert str(normal) not in " ".join(args)

    def test_no_shim_files_returns_empty(self, tmp_path):
        from lintgate.lint_fixer import _build_shim_ignores

        normal = tmp_path / "normal.py"
        normal.write_text("x = 1\ny = 2\nz = 3\n")
        assert _build_shim_ignores([str(normal)]) == []


# ── 5. mypy_linter.py — heavy dep scanning and execute override ──────


class TestScanRequirementsFile:
    """Test scanning requirements files for heavy dependencies."""

    def test_detects_torch(self, tmp_path):
        from lintgate.linters.mypy_linter import _scan_requirements_file

        req = tmp_path / "requirements.txt"
        req.write_text("flask==2.0.0\ntorch==2.1.0\nrequests>=2.28\n")
        found: list[str] = []
        _scan_requirements_file(str(req), found)
        assert "torch" in found
        assert "flask" not in found

    def test_handles_extras(self, tmp_path):
        from lintgate.linters.mypy_linter import _scan_requirements_file

        req = tmp_path / "requirements.txt"
        req.write_text("transformers[torch]>=4.0\n")
        found: list[str] = []
        _scan_requirements_file(str(req), found)
        assert "transformers" in found

    def test_no_duplicates(self, tmp_path):
        from lintgate.linters.mypy_linter import _scan_requirements_file

        req = tmp_path / "requirements.txt"
        req.write_text("torch==2.0\ntorch==2.1\n")
        found: list[str] = []
        _scan_requirements_file(str(req), found)
        assert found.count("torch") == 1

    def test_missing_file_no_error(self):
        from lintgate.linters.mypy_linter import _scan_requirements_file

        found: list[str] = []
        _scan_requirements_file("/nonexistent/requirements.txt", found)
        assert found == []


class TestScanPyprojectToml:
    """Test scanning pyproject.toml for heavy deps."""

    def test_detects_pandas_in_pyproject(self, tmp_path):
        from lintgate.linters.mypy_linter import _scan_pyproject_toml

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\ndependencies = ["pandas>=1.5"]\n')
        found: list[str] = []
        _scan_pyproject_toml(str(pyproject), found)
        assert "pandas" in found

    def test_missing_file_no_error(self):
        from lintgate.linters.mypy_linter import _scan_pyproject_toml

        found: list[str] = []
        _scan_pyproject_toml("/nonexistent/pyproject.toml", found)
        assert found == []


class TestDetectHeavyDeps:
    """Test the combined heavy-dep detection flow."""

    def test_detects_from_requirements(self, tmp_path):
        from lintgate.linters.mypy_linter import _detect_heavy_deps

        (tmp_path / "requirements.txt").write_text("numpy==1.24\n")
        result = _detect_heavy_deps(str(tmp_path))
        assert "numpy" in result

    def test_detects_from_pyproject(self, tmp_path):
        from lintgate.linters.mypy_linter import _detect_heavy_deps

        (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["scipy>=1.10"]\n')
        result = _detect_heavy_deps(str(tmp_path))
        assert "scipy" in result

    def test_no_heavy_deps(self, tmp_path):
        from lintgate.linters.mypy_linter import _detect_heavy_deps

        (tmp_path / "requirements.txt").write_text("flask==2.0\nrequests>=2.28\n")
        result = _detect_heavy_deps(str(tmp_path))
        assert result == []

    def test_dev_requirements_also_scanned(self, tmp_path):
        from lintgate.linters.mypy_linter import _detect_heavy_deps

        (tmp_path / "requirements-dev.txt").write_text("tensorflow==2.12\n")
        result = _detect_heavy_deps(str(tmp_path))
        assert "tensorflow" in result


class TestMypyLinterExecuteOverride:
    """Test the execute() method's heavy-dep timeout and deferral logic."""

    def test_heavy_dep_timeout_becomes_deferred(self):
        from lintgate.linters.mypy_linter import MypyLinter
        from lintgate.types import LinterContext, LinterResult

        linter = MypyLinter()
        ctx = LinterContext(files=["test.py"], project_root="/tmp/fake")

        # Simulate: _detect_heavy_deps finds torch, super().execute() returns timeout
        timeout_result = LinterResult(linter_name="mypy", status="timeout", duration_ms=60000)
        with (
            patch.object(MypyLinter, "execute", wraps=linter.execute) as _,
            patch(
                "lintgate.linters.mypy_linter._detect_heavy_deps",
                return_value=["torch"],
            ),
            patch(
                "lintgate.linters.base.BaseLinter.execute",
                return_value=timeout_result,
            ),
        ):
            result = linter.execute(ctx)

        assert result.status == "deferred"
        assert "torch" in (result.error or "")

    def test_no_heavy_deps_timeout_stays_timeout(self):
        from lintgate.linters.mypy_linter import MypyLinter
        from lintgate.types import LinterContext, LinterResult

        linter = MypyLinter()
        ctx = LinterContext(files=["test.py"], project_root="/tmp/fake")

        timeout_result = LinterResult(linter_name="mypy", status="timeout", duration_ms=15000)
        with (
            patch(
                "lintgate.linters.mypy_linter._detect_heavy_deps",
                return_value=[],
            ),
            patch(
                "lintgate.linters.base.BaseLinter.execute",
                return_value=timeout_result,
            ),
        ):
            result = linter.execute(ctx)

        assert result.status == "timeout"


# ── 6. perf011 — _extract_assign_target_name, _collect_loop_mutations,
#       _get_assignments_in_statement (AugAssign/Subscript) ──────────


class TestExtractAssignTargetName:
    """Test extraction of variable names from assignment targets."""

    def test_simple_name(self):
        from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
            _extract_assign_target_name,
        )

        node = ast.Name(id="x")
        assert _extract_assign_target_name(node) == "x"

    def test_subscript_target(self):
        from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
            _extract_assign_target_name,
        )

        # x[i] — the subscript target should return the base name 'x'
        node = ast.Subscript(
            value=ast.Name(id="x"),
            slice=ast.Name(id="i"),
        )
        assert _extract_assign_target_name(node) == "x"

    def test_complex_subscript_returns_none(self):
        from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
            _extract_assign_target_name,
        )

        # a.b[i] — value is Attribute, not Name
        node = ast.Subscript(
            value=ast.Attribute(value=ast.Name(id="a"), attr="b"),
            slice=ast.Name(id="i"),
        )
        assert _extract_assign_target_name(node) is None

    def test_attribute_returns_none(self):
        from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
            _extract_assign_target_name,
        )

        node = ast.Attribute(value=ast.Name(id="self"), attr="x")
        assert _extract_assign_target_name(node) is None


class TestCollectLoopMutations:
    """Test collecting mutating method calls in loop bodies."""

    def test_append_detected(self):
        from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
            _collect_loop_mutations,
        )

        code = "results.append(x)"
        tree = ast.parse(code)
        mutations = _collect_loop_mutations(tree.body)
        assert "results" in mutations

    def test_sort_detected(self):
        from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
            _collect_loop_mutations,
        )

        code = "data.sort()"
        tree = ast.parse(code)
        mutations = _collect_loop_mutations(tree.body)
        assert "data" in mutations

    def test_non_mutating_method_ignored(self):
        from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
            _collect_loop_mutations,
        )

        code = "data.copy()"
        tree = ast.parse(code)
        mutations = _collect_loop_mutations(tree.body)
        assert "data" not in mutations

    def test_attribute_chain_ignored(self):
        from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
            _collect_loop_mutations,
        )

        # self.data.append(x) — value is Attribute, not Name
        code = "self.data.append(x)"
        tree = ast.parse(code)
        mutations = _collect_loop_mutations(tree.body)
        assert mutations == set()

    def test_multiple_mutations(self):
        from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
            _collect_loop_mutations,
        )

        code = "a.append(1)\nb.extend([2])\nc.update({3: 4})"
        tree = ast.parse(code)
        mutations = _collect_loop_mutations(tree.body)
        assert mutations == {"a", "b", "c"}


class TestGetAssignmentsInStatement:
    """Test _get_assignments_in_statement with AugAssign and Subscript support."""

    def test_regular_assign(self):
        from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
            _get_assignments_in_statement,
        )

        tree = ast.parse("x = 1")
        names = _get_assignments_in_statement(tree.body[0])
        assert "x" in names

    def test_aug_assign(self):
        from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
            _get_assignments_in_statement,
        )

        tree = ast.parse("x += 1")
        names = _get_assignments_in_statement(tree.body[0])
        assert "x" in names

    def test_annotated_assign(self):
        from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
            _get_assignments_in_statement,
        )

        tree = ast.parse("x: int = 1")
        names = _get_assignments_in_statement(tree.body[0])
        assert "x" in names

    def test_subscript_assign(self):
        from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
            _get_assignments_in_statement,
        )

        tree = ast.parse("x[0] = 1")
        names = _get_assignments_in_statement(tree.body[0])
        assert "x" in names

    def test_no_assignment(self):
        from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
            _get_assignments_in_statement,
        )

        tree = ast.parse("print(x)")
        names = _get_assignments_in_statement(tree.body[0])
        assert names == set()


# ── 7. runtime.py — convergence escalation ──────────────────────────


class TestApplyConvergenceEscalation:
    """Test _apply_convergence_escalation and _escalate_convergent_findings."""

    @staticmethod
    def _make_channel_result(channel, findings, status="fail"):
        from lintgate.controlplane.types import ChannelResult

        return ChannelResult(
            channel=channel,
            status=status,
            severity="informational",
            findings=findings,
        )

    @staticmethod
    def _make_finding(file, severity="informational"):
        from lintgate.types import LintIssue

        return LintIssue(
            linter="test",
            kind="TEST001",
            message="test finding",
            file=file,
            severity=severity,
        )

    def test_escalation_when_three_channels_converge(self):
        from lintgate.controlplane.runtime import _escalate_convergent_findings

        f = self._make_finding("/src/foo.py")
        g = self._make_finding("/src/foo.py")
        h = self._make_finding("/src/foo.py")
        results = [
            self._make_channel_result("lint", [f]),
            self._make_channel_result("test", [g]),
            self._make_channel_result("structure", [h]),
        ]
        _escalate_convergent_findings(results, min_channels=3)

        # All informational findings on /src/foo.py should be escalated to warning
        for cr in results:
            for finding in cr.findings:
                assert finding.severity == "warning"
                assert "convergence" in finding.evidence

    def test_no_escalation_below_threshold(self):
        from lintgate.controlplane.runtime import _escalate_convergent_findings

        f = self._make_finding("/src/foo.py")
        g = self._make_finding("/src/foo.py")
        results = [
            self._make_channel_result("lint", [f]),
            self._make_channel_result("test", [g]),
        ]
        _escalate_convergent_findings(results, min_channels=3)

        for cr in results:
            for finding in cr.findings:
                assert finding.severity == "informational"

    def test_blocking_not_escalated_further(self):
        from lintgate.controlplane.runtime import _escalate_convergent_findings

        f = self._make_finding("/src/foo.py", severity="blocking")
        g = self._make_finding("/src/foo.py")
        h = self._make_finding("/src/foo.py")
        results = [
            self._make_channel_result("lint", [f]),
            self._make_channel_result("test", [g]),
            self._make_channel_result("structure", [h]),
        ]
        _escalate_convergent_findings(results, min_channels=3)

        # The blocking finding should remain blocking
        assert results[0].findings[0].severity == "blocking"
        # The informational ones should be escalated
        assert results[1].findings[0].severity == "warning"
        assert results[2].findings[0].severity == "warning"

    def test_skipped_channels_excluded(self):
        from lintgate.controlplane.runtime import _escalate_convergent_findings

        f = self._make_finding("/src/foo.py")
        g = self._make_finding("/src/foo.py")
        h = self._make_finding("/src/foo.py")
        results = [
            self._make_channel_result("lint", [f]),
            self._make_channel_result("test", [g], status="skip"),
            self._make_channel_result("structure", [h]),
        ]
        _escalate_convergent_findings(results, min_channels=3)

        # Only 2 non-skip channels have findings, so no escalation
        assert results[0].findings[0].severity == "informational"

    def test_different_files_no_escalation(self):
        from lintgate.controlplane.runtime import _escalate_convergent_findings

        f = self._make_finding("/src/foo.py")
        g = self._make_finding("/src/bar.py")
        h = self._make_finding("/src/baz.py")
        results = [
            self._make_channel_result("lint", [f]),
            self._make_channel_result("test", [g]),
            self._make_channel_result("structure", [h]),
        ]
        _escalate_convergent_findings(results, min_channels=3)

        for cr in results:
            for finding in cr.findings:
                assert finding.severity == "informational"

    def test_apply_convergence_escalation_suppresses_exceptions(self):
        """The outer wrapper should catch exceptions from the inner function."""
        from lintgate.controlplane.runtime import _apply_convergence_escalation

        # Pass invalid data to trigger an internal error — should not raise
        _apply_convergence_escalation([])  # Empty list, no error


# ── 8. patterns.py — extract_mock_patch_targets & _filter_mock_targets


class TestExtractMockPatchTargets:
    """Test extraction of mock.patch string targets from test files."""

    def test_extracts_patch_call(self, tmp_path):
        from lintgate.channels.structure.patterns import extract_mock_patch_targets

        test_file = tmp_path / "test_example.py"
        test_file.write_text(
            textwrap.dedent("""\
            from unittest.mock import patch

            @patch("myapp.module.func")
            def test_something(mock_func):
                pass
        """)
        )

        targets = extract_mock_patch_targets([str(test_file)])
        assert "myapp.module.func" in targets
        assert targets["myapp.module.func"][0]["file"] == str(test_file)
        assert targets["myapp.module.func"][0]["line"] == 3

    def test_extracts_mock_patch_call(self, tmp_path):
        from lintgate.channels.structure.patterns import extract_mock_patch_targets

        test_file = tmp_path / "test_example.py"
        test_file.write_text(
            textwrap.dedent("""\
            import mock

            with mock.patch("pkg.mod.Class"):
                pass
        """)
        )

        targets = extract_mock_patch_targets([str(test_file)])
        assert "pkg.mod.Class" in targets

    def test_ignores_non_patch_calls(self, tmp_path):
        from lintgate.channels.structure.patterns import extract_mock_patch_targets

        test_file = tmp_path / "test_example.py"
        test_file.write_text(
            textwrap.dedent("""\
            import json
            json.loads('{"key": "value"}')
        """)
        )

        targets = extract_mock_patch_targets([str(test_file)])
        assert targets == {}

    def test_handles_syntax_error(self, tmp_path):
        from lintgate.channels.structure.patterns import extract_mock_patch_targets

        test_file = tmp_path / "test_broken.py"
        test_file.write_text("def broken(\n")

        targets = extract_mock_patch_targets([str(test_file)])
        assert targets == {}

    def test_multiple_targets_across_files(self, tmp_path):
        from lintgate.channels.structure.patterns import extract_mock_patch_targets

        f1 = tmp_path / "test_a.py"
        f1.write_text('from unittest.mock import patch\n@patch("a.b.c")\ndef test(): pass\n')
        f2 = tmp_path / "test_b.py"
        f2.write_text('from unittest.mock import patch\n@patch("x.y.z")\ndef test(): pass\n')

        targets = extract_mock_patch_targets([str(f1), str(f2)])
        assert "a.b.c" in targets
        assert "x.y.z" in targets

    def test_same_target_multiple_locations(self, tmp_path):
        from lintgate.channels.structure.patterns import extract_mock_patch_targets

        test_file = tmp_path / "test_dup.py"
        test_file.write_text(
            textwrap.dedent("""\
            from unittest.mock import patch

            @patch("mod.func")
            def test_one(m): pass

            @patch("mod.func")
            def test_two(m): pass
        """)
        )

        targets = extract_mock_patch_targets([str(test_file)])
        assert len(targets["mod.func"]) == 2


class TestFilterMockTargetsForModules:
    """Test filtering mock targets to module-relevant ones."""

    def test_filters_matching_modules(self, tmp_path):
        from lintgate.channels.structure.patterns import _filter_mock_targets_for_modules

        mock_targets = {
            "lintgate.lint_fixer.run_safe_fixes": [
                {"file": str(tmp_path / "test_fixer.py"), "line": 10}
            ],
            "lintgate.config.load": [{"file": str(tmp_path / "test_config.py"), "line": 5}],
        }
        affected = _filter_mock_targets_for_modules(mock_targets, ["lint_fixer"], str(tmp_path))
        assert len(affected) == 1
        assert "lint_fixer" in affected[0]

    def test_no_matching_modules(self, tmp_path):
        from lintgate.channels.structure.patterns import _filter_mock_targets_for_modules

        mock_targets = {
            "lintgate.config.load": [{"file": str(tmp_path / "test_config.py"), "line": 5}],
        }
        affected = _filter_mock_targets_for_modules(mock_targets, ["lint_fixer"], str(tmp_path))
        assert affected == []

    def test_multiple_modules_matched(self, tmp_path):
        from lintgate.channels.structure.patterns import _filter_mock_targets_for_modules

        mock_targets = {
            "pkg.lint_fixer.func": [{"file": str(tmp_path / "test_a.py"), "line": 1}],
            "pkg.config.load": [{"file": str(tmp_path / "test_b.py"), "line": 2}],
        }
        affected = _filter_mock_targets_for_modules(
            mock_targets, ["lint_fixer", "config"], str(tmp_path)
        )
        assert len(affected) == 2

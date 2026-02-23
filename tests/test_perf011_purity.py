"""Tests for PERF011 three-tier purity resolution system."""

from __future__ import annotations

import ast
import textwrap

from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
    _analyze_file_purity,
    _is_known_pure,
    check_pure_uncached_in_loop,
    set_manifest_pure_names,
)
from lintgate.linters.performance_checks.purity import _KNOWN_PURE_BUILTINS


# ── set_manifest_pure_names ─────────────────────────────────────────


class TestSetManifestPureNames:
    def setup_method(self):
        set_manifest_pure_names(None)

    def teardown_method(self):
        set_manifest_pure_names(None)

    def test_set_and_clear(self):
        set_manifest_pure_names({"my_func"})
        assert _is_known_pure("my_func")[0] is True
        set_manifest_pure_names(None)
        assert _is_known_pure("my_func")[0] is False


# ── _is_known_pure ──────────────────────────────────────────────────


class TestIsKnownPure:
    def setup_method(self):
        set_manifest_pure_names(None)

    def teardown_method(self):
        set_manifest_pure_names(None)

    def test_builtin_pure(self):
        is_pure, source = _is_known_pure("len")
        assert is_pure is True
        assert source == "builtin"

    def test_builtin_sorted(self):
        is_pure, source = _is_known_pure("sorted")
        assert is_pure is True
        assert source == "builtin"

    def test_manifest_pure(self):
        set_manifest_pure_names({"project.compute_hash"})
        is_pure, source = _is_known_pure("project.compute_hash")
        assert is_pure is True
        assert source == "manifest"

    def test_local_purity(self):
        local = {"local_helper"}
        is_pure, source = _is_known_pure("local_helper", local_pure_names=local)
        assert is_pure is True
        assert source == "local_purity"

    def test_unknown_function(self):
        is_pure, source = _is_known_pure("unknown_function")
        assert is_pure is False
        assert source == ""

    def test_priority_builtin_over_manifest(self):
        """Builtins are checked first, even if manifest also has the name."""
        set_manifest_pure_names({"len"})
        is_pure, source = _is_known_pure("len")
        assert source == "builtin"

    def test_priority_manifest_over_local(self):
        """Manifest is checked before local purity."""
        set_manifest_pure_names({"shared_fn"})
        is_pure, source = _is_known_pure("shared_fn", local_pure_names={"shared_fn"})
        assert source == "manifest"


# ── _analyze_file_purity ────────────────────────────────────────────


class TestAnalyzeFilePurity:
    def test_detects_pure_function(self):
        source = textwrap.dedent("""\
            def add(a, b):
                return a + b
        """)
        tree = ast.parse(source)
        pure_names = _analyze_file_purity(tree)
        assert "add" in pure_names

    def test_detects_impure_function(self):
        source = textwrap.dedent("""\
            data = []
            def append_to_global(x):
                data.append(x)
        """)
        tree = ast.parse(source)
        pure_names = _analyze_file_purity(tree)
        assert "append_to_global" not in pure_names

    def test_empty_module(self):
        tree = ast.parse("")
        pure_names = _analyze_file_purity(tree)
        assert pure_names == set()


# ── PERF011 integration: detection with three-tier resolution ───────


class TestPERF011Detection:
    def setup_method(self):
        set_manifest_pure_names(None)

    def teardown_method(self):
        set_manifest_pure_names(None)

    def _check(self, source: str):
        from lintgate.linters.performance_checks._helpers import attach_parents

        source = textwrap.dedent(source)
        tree = ast.parse(source, filename="<test>")
        attach_parents(tree)
        return list(check_pure_uncached_in_loop(tree, "<test>"))

    def test_detects_builtin_pure_with_invariant_args(self):
        """len(data) where data is NOT the loop var → loop-invariant → PERF011."""
        issues = self._check("""\
            data = [1, 2, 3]
            for i in range(10):
                x = len(data)
        """)
        perf011 = [i for i in issues if i.kind == "PERF011"]
        assert len(perf011) >= 1
        assert perf011[0].evidence["source"] == "builtin"

    def test_skips_builtin_with_loop_variant_args(self):
        """len(item) where item IS the loop var → not invariant → no PERF011."""
        issues = self._check("""\
            for item in items:
                x = len(item)
        """)
        perf011 = [i for i in issues if i.kind == "PERF011"]
        assert len(perf011) == 0

    def test_detects_manifest_pure_with_invariant_args(self):
        set_manifest_pure_names({"compute_score"})
        issues = self._check("""\
            config = "default"
            for i in range(10):
                x = compute_score(config)
        """)
        perf011 = [i for i in issues if i.kind == "PERF011"]
        assert len(perf011) >= 1
        assert perf011[0].evidence["source"] == "manifest"

    def test_skips_unknown_function(self):
        issues = self._check("""\
            config = "x"
            for i in range(10):
                x = unknown_func(config)
        """)
        perf011 = [i for i in issues if i.kind == "PERF011"]
        assert len(perf011) == 0

    def test_skips_no_args(self):
        """Pure call with zero args is not flagged (nothing to hoist)."""
        issues = self._check("""\
            for i in range(10):
                x = len()
        """)
        perf011 = [i for i in issues if i.kind == "PERF011"]
        assert len(perf011) == 0

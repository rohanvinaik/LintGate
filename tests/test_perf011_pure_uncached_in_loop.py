"""Tests for PERF011 three-tier purity resolution system."""

from __future__ import annotations

import ast
import textwrap

from lintgate.linters.performance_checks.perf011_pure_uncached_in_loop import (
    _analyze_file_purity,
    _analyze_loop_body_for_uncached_calls,
    _check_all_args_invariant,
    _check_call_in_loop,
    _check_keyword_args_invariant,
    _check_positional_args_invariant,
    _collect_loop_assignments,
    _collect_loop_mutations,
    _extract_assign_target_name,
    _get_assignments_in_statement,
    _get_loop_targets,
    _is_known_pure,
    _is_loop_invariant,
    check_pure_uncached_in_loop,
    set_manifest_pure_names,
)

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


# ── _is_loop_invariant ─────────────────────────────────────────────


class TestIsLoopInvariant:
    def test_constant_is_invariant(self):
        node = ast.parse("42", mode="eval").body
        assert _is_loop_invariant(node, {"x", "y"}) is True

    def test_string_constant_is_invariant(self):
        node = ast.parse('"hello"', mode="eval").body
        assert _is_loop_invariant(node, {"i"}) is True

    def test_name_in_targets_is_variant(self):
        node = ast.parse("x", mode="eval").body
        assert _is_loop_invariant(node, {"x", "y"}) is False

    def test_name_not_in_targets_is_invariant(self):
        node = ast.parse("z", mode="eval").body
        assert _is_loop_invariant(node, {"x", "y"}) is True

    def test_nested_name_in_targets_is_variant(self):
        """A subexpression referencing a loop target makes the whole arg variant."""
        node = ast.parse("a + x", mode="eval").body
        assert _is_loop_invariant(node, {"x"}) is False

    def test_empty_targets_always_invariant(self):
        node = ast.parse("anything", mode="eval").body
        assert _is_loop_invariant(node, set()) is True


# ── _get_loop_targets ──────────────────────────────────────────────


class TestGetLoopTargets:
    def test_for_simple_target(self):
        tree = ast.parse("for x in items: pass")
        loop = tree.body[0]
        assert _get_loop_targets(loop) == {"x"}

    def test_for_tuple_unpacking(self):
        tree = ast.parse("for a, b in pairs: pass")
        loop = tree.body[0]
        assert _get_loop_targets(loop) == {"a", "b"}

    def test_for_list_unpacking(self):
        tree = ast.parse("for [c, d] in pairs: pass")
        loop = tree.body[0]
        assert _get_loop_targets(loop) == {"c", "d"}

    def test_while_returns_empty(self):
        tree = ast.parse("while True: pass")
        loop = tree.body[0]
        assert _get_loop_targets(loop) == set()

    def test_non_loop_returns_empty(self):
        tree = ast.parse("x = 1")
        stmt = tree.body[0]
        assert _get_loop_targets(stmt) == set()


# ── _extract_assign_target_name ────────────────────────────────────


class TestExtractAssignTargetName:
    def test_simple_name(self):
        tree = ast.parse("x = 1")
        target = tree.body[0].targets[0]
        assert _extract_assign_target_name(target) == "x"

    def test_subscript_target(self):
        tree = ast.parse("data[0] = 1")
        target = tree.body[0].targets[0]
        assert _extract_assign_target_name(target) == "data"

    def test_attribute_target_returns_none(self):
        tree = ast.parse("obj.attr = 1")
        target = tree.body[0].targets[0]
        assert _extract_assign_target_name(target) is None

    def test_tuple_target_returns_none(self):
        tree = ast.parse("a, b = 1, 2")
        target = tree.body[0].targets[0]
        assert _extract_assign_target_name(target) is None


# ── _get_assignments_in_statement ──────────────────────────────────


class TestGetAssignmentsInStatement:
    def test_simple_assign(self):
        tree = ast.parse("x = 1")
        assert _get_assignments_in_statement(tree.body[0]) == {"x"}

    def test_annotated_assign(self):
        tree = ast.parse("x: int = 5")
        assert _get_assignments_in_statement(tree.body[0]) == {"x"}

    def test_augmented_assign(self):
        tree = ast.parse("x += 1")
        assert _get_assignments_in_statement(tree.body[0]) == {"x"}

    def test_subscript_assign(self):
        tree = ast.parse("data[0] = 99")
        assert _get_assignments_in_statement(tree.body[0]) == {"data"}

    def test_no_assignments(self):
        tree = ast.parse("print(x)")
        assert _get_assignments_in_statement(tree.body[0]) == set()

    def test_multiple_targets(self):
        tree = ast.parse("a = b = 1")
        assert _get_assignments_in_statement(tree.body[0]) == {"a", "b"}


# ── _collect_loop_assignments ──────────────────────────────────────


class TestCollectLoopAssignments:
    def test_collects_from_body(self):
        tree = ast.parse("x = 1\ny += 2\nz: int = 3")
        assert _collect_loop_assignments(tree.body) == {"x", "y", "z"}

    def test_empty_body(self):
        assert _collect_loop_assignments([]) == set()

    def test_no_assignments_in_body(self):
        tree = ast.parse("print(1)\nprint(2)")
        assert _collect_loop_assignments(tree.body) == set()


# ── _collect_loop_mutations ────────────────────────────────────────


class TestCollectLoopMutations:
    def test_append_mutation(self):
        tree = ast.parse("results.append(1)")
        assert _collect_loop_mutations(tree.body) == {"results"}

    def test_update_mutation(self):
        tree = ast.parse("data.update({'k': 'v'})")
        assert _collect_loop_mutations(tree.body) == {"data"}

    def test_non_mutating_method_ignored(self):
        tree = ast.parse("data.copy()")
        assert _collect_loop_mutations(tree.body) == set()

    def test_multiple_mutations(self):
        tree = ast.parse("a.append(1)\nb.extend([2])\nc.sort()")
        assert _collect_loop_mutations(tree.body) == {"a", "b", "c"}

    def test_chained_attribute_ignored(self):
        """obj.nested.append(x) — receiver is not a simple Name."""
        tree = ast.parse("obj.nested.append(1)")
        assert _collect_loop_mutations(tree.body) == set()

    def test_empty_body(self):
        assert _collect_loop_mutations([]) == set()


# ── _check_positional_args_invariant ───────────────────────────────


class TestCheckPositionalArgsInvariant:
    def test_all_constants(self):
        tree = ast.parse("f(1, 2, 3)", mode="eval")
        call = tree.body
        assert _check_positional_args_invariant(call.args, {"x"}) is True

    def test_one_variant_arg(self):
        tree = ast.parse("f(1, x)", mode="eval")
        call = tree.body
        assert _check_positional_args_invariant(call.args, {"x"}) is False

    def test_empty_args(self):
        assert _check_positional_args_invariant([], {"x"}) is True

    def test_name_outside_targets(self):
        tree = ast.parse("f(z)", mode="eval")
        call = tree.body
        assert _check_positional_args_invariant(call.args, {"x"}) is True


# ── _check_keyword_args_invariant ──────────────────────────────────


class TestCheckKeywordArgsInvariant:
    def test_all_constant_kwargs(self):
        tree = ast.parse("f(a=1, b=2)", mode="eval")
        call = tree.body
        assert _check_keyword_args_invariant(call.keywords, {"x"}) is True

    def test_one_variant_kwarg(self):
        tree = ast.parse("f(a=x)", mode="eval")
        call = tree.body
        assert _check_keyword_args_invariant(call.keywords, {"x"}) is False

    def test_empty_kwargs(self):
        assert _check_keyword_args_invariant([], {"x"}) is True

    def test_kwarg_references_external_name(self):
        tree = ast.parse("f(key=z)", mode="eval")
        call = tree.body
        assert _check_keyword_args_invariant(call.keywords, {"x"}) is True


# ── _check_all_args_invariant ──────────────────────────────────────


class TestCheckAllArgsInvariant:
    def test_all_invariant(self):
        tree = ast.parse("f(1, key=2)", mode="eval")
        call = tree.body
        assert _check_all_args_invariant(call, {"x"}) is True

    def test_positional_variant(self):
        tree = ast.parse("f(x, key=2)", mode="eval")
        call = tree.body
        assert _check_all_args_invariant(call, {"x"}) is False

    def test_keyword_variant(self):
        tree = ast.parse("f(1, key=x)", mode="eval")
        call = tree.body
        assert _check_all_args_invariant(call, {"x"}) is False

    def test_both_variant(self):
        tree = ast.parse("f(x, key=x)", mode="eval")
        call = tree.body
        assert _check_all_args_invariant(call, {"x"}) is False


# ── _check_call_in_loop ────────────────────────────────────────────


class TestCheckCallInLoop:
    def setup_method(self):
        set_manifest_pure_names(None)

    def teardown_method(self):
        set_manifest_pure_names(None)

    def test_pure_builtin_invariant_returns_issue(self):
        tree = ast.parse("len(data)", mode="eval")
        call = tree.body
        issue = _check_call_in_loop(call, set(), "test.py")
        assert issue is not None
        assert issue.kind == "PERF011"
        assert issue.confidence == 0.8
        assert issue.evidence["source"] == "builtin"

    def test_pure_manifest_returns_lower_confidence(self):
        set_manifest_pure_names({"compute"})
        tree = ast.parse("compute(cfg)", mode="eval")
        call = tree.body
        issue = _check_call_in_loop(call, set(), "test.py")
        assert issue is not None
        assert issue.confidence == 0.7
        assert issue.evidence["source"] == "manifest"

    def test_unknown_func_returns_none(self):
        tree = ast.parse("mystery(data)", mode="eval")
        call = tree.body
        issue = _check_call_in_loop(call, set(), "test.py")
        assert issue is None

    def test_no_args_returns_none(self):
        tree = ast.parse("len()", mode="eval")
        call = tree.body
        issue = _check_call_in_loop(call, set(), "test.py")
        assert issue is None

    def test_variant_args_returns_none(self):
        tree = ast.parse("len(x)", mode="eval")
        call = tree.body
        issue = _check_call_in_loop(call, {"x"}, "test.py")
        assert issue is None

    def test_local_pure_returns_issue(self):
        tree = ast.parse("helper(cfg)", mode="eval")
        call = tree.body
        issue = _check_call_in_loop(call, set(), "test.py", local_pure_names={"helper"})
        assert issue is not None
        assert issue.confidence == 0.7
        assert issue.evidence["source"] == "local_purity"


# ── _analyze_loop_body_for_uncached_calls ──────────────────────────


class TestAnalyzeLoopBodyForUncachedCalls:
    def setup_method(self):
        set_manifest_pure_names(None)

    def teardown_method(self):
        set_manifest_pure_names(None)

    def test_finds_pure_call_in_body(self):
        tree = ast.parse("x = len(data)")
        issues = list(
            _analyze_loop_body_for_uncached_calls(tree.body, set(), "test.py")
        )
        assert len(issues) == 1
        assert issues[0].evidence["func"] == "len"

    def test_skips_impure_call(self):
        tree = ast.parse("x = unknown(data)")
        issues = list(
            _analyze_loop_body_for_uncached_calls(tree.body, set(), "test.py")
        )
        assert len(issues) == 0

    def test_multiple_calls_in_body(self):
        tree = ast.parse("x = len(a)\ny = sorted(b)")
        issues = list(
            _analyze_loop_body_for_uncached_calls(tree.body, set(), "test.py")
        )
        funcs = {i.evidence["func"] for i in issues}
        assert "len" in funcs
        assert "sorted" in funcs

    def test_variant_call_skipped(self):
        tree = ast.parse("x = len(i)")
        issues = list(
            _analyze_loop_body_for_uncached_calls(tree.body, {"i"}, "test.py")
        )
        assert len(issues) == 0

    def test_empty_body(self):
        issues = list(
            _analyze_loop_body_for_uncached_calls([], set(), "test.py")
        )
        assert len(issues) == 0

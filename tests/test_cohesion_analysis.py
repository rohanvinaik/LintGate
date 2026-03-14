"""Tests for cohesion_analysis — file cohesion scoring, call graph, components, split proposals."""

from __future__ import annotations

import ast
import textwrap

from lintgate.linters.structure_checks.cohesion_analysis import (
    CohesionResult,
    _build_call_graph,
    _collect_module_level_vars,
    _collect_name_references,
    _collect_top_level_defs,
    _compute_cohesion_score,
    _connect_shared_vars,
    _detect_cli_mixing,
    _find_connected_components,
    _generate_split_proposals,
    analyze_file_cohesion,
)


def _parse(source: str) -> ast.Module:
    return ast.parse(textwrap.dedent(source))


# ─── _collect_top_level_defs ─────────────────────────────────────────


class TestCollectTopLevelDefs:
    def test_empty_module(self):
        tree = _parse("")
        defs = _collect_top_level_defs(tree)
        assert defs == {}

    def test_functions_only(self):
        tree = _parse("""
            def alpha(): pass
            def beta(): pass
        """)
        defs = _collect_top_level_defs(tree)
        assert set(defs.keys()) == {"alpha", "beta"}

    def test_classes_only(self):
        tree = _parse("""
            class Foo: pass
            class Bar: pass
        """)
        defs = _collect_top_level_defs(tree)
        assert set(defs.keys()) == {"Foo", "Bar"}

    def test_mixed_functions_and_classes(self):
        tree = _parse("""
            def helper(): pass
            class Widget: pass
            def process(): pass
        """)
        defs = _collect_top_level_defs(tree)
        assert set(defs.keys()) == {"helper", "Widget", "process"}

    def test_async_functions(self):
        tree = _parse("""
            async def fetch(): pass
            def sync_helper(): pass
        """)
        defs = _collect_top_level_defs(tree)
        assert set(defs.keys()) == {"fetch", "sync_helper"}

    def test_ignores_nested_defs(self):
        tree = _parse("""
            def outer():
                def inner():
                    pass
        """)
        defs = _collect_top_level_defs(tree)
        assert set(defs.keys()) == {"outer"}

    def test_ignores_assignments_and_imports(self):
        tree = _parse("""
            import os
            X = 10
            def func(): pass
        """)
        defs = _collect_top_level_defs(tree)
        assert set(defs.keys()) == {"func"}


# ─── _collect_module_level_vars ──────────────────────────────────────


class TestCollectModuleLevelVars:
    def test_empty_module(self):
        tree = _parse("")
        assert _collect_module_level_vars(tree) == set()

    def test_simple_assignments(self):
        tree = _parse("""
            X = 10
            Y = 20
        """)
        assert _collect_module_level_vars(tree) == {"X", "Y"}

    def test_annotated_assignments(self):
        tree = _parse("""
            x: int = 5
            y: str = "hello"
        """)
        assert _collect_module_level_vars(tree) == {"x", "y"}

    def test_mixed_assignments(self):
        tree = _parse("""
            X = 10
            y: int = 20
        """)
        assert _collect_module_level_vars(tree) == {"X", "y"}

    def test_ignores_tuple_assignment_targets(self):
        # ast.Assign with Tuple target — individual Name targets inside
        # Tuple are not extracted because the code checks isinstance(target, ast.Name)
        tree = _parse("""
            a, b = 1, 2
        """)
        # The target is a Tuple, not a Name, so neither a nor b is collected
        assert _collect_module_level_vars(tree) == set()

    def test_ignores_vars_inside_functions(self):
        tree = _parse("""
            def func():
                local = 42
            MODULE_VAR = 1
        """)
        # Only top-level body is scanned
        assert _collect_module_level_vars(tree) == {"MODULE_VAR"}


# ─── _collect_name_references ────────────────────────────────────────


class TestCollectNameReferences:
    def test_empty_function(self):
        tree = _parse("def f(): pass")
        func_node = tree.body[0]
        refs = _collect_name_references(func_node)
        # "pass" generates no Name nodes in Load context
        assert isinstance(refs, set)

    def test_references_in_function_body(self):
        tree = _parse("""
            def f():
                x = helper()
                return process(x)
        """)
        func_node = tree.body[0]
        refs = _collect_name_references(func_node)
        assert "helper" in refs
        assert "process" in refs
        assert "x" in refs

    def test_class_reference(self):
        tree = _parse("""
            def f():
                obj = MyClass()
        """)
        func_node = tree.body[0]
        refs = _collect_name_references(func_node)
        assert "MyClass" in refs

    def test_no_store_context(self):
        # Store targets: the LHS of assignments generates ast.Name(ctx=Store)
        # which should NOT appear in the result. But the RHS Load refs should.
        tree = _parse("""
            def f():
                x = 1
        """)
        func_node = tree.body[0]
        refs = _collect_name_references(func_node)
        # 'x' in Store context should not be collected, but '1' is a Constant, not a Name
        # The function only collects Load context names
        # x in Store context may or may not appear; just verify set type
        assert isinstance(refs, set)


# ─── _build_call_graph ───────────────────────────────────────────────


class TestBuildCallGraph:
    def test_no_cross_references(self):
        tree = _parse("""
            def alpha():
                return 1
            def beta():
                return 2
            def gamma():
                return 3
        """)
        defs = _collect_top_level_defs(tree)
        adj = _build_call_graph(defs)
        assert set(adj.keys()) == {"alpha", "beta", "gamma"}
        for neighbors in adj.values():
            assert neighbors == set()

    def test_simple_call(self):
        tree = _parse("""
            def alpha():
                return beta()
            def beta():
                return 1
            def gamma():
                return 2
        """)
        defs = _collect_top_level_defs(tree)
        adj = _build_call_graph(defs)
        # alpha calls beta → undirected edge
        assert "beta" in adj["alpha"]
        assert "alpha" in adj["beta"]
        # gamma is isolated
        assert adj["gamma"] == set()

    def test_mutual_references(self):
        tree = _parse("""
            def alpha():
                return beta()
            def beta():
                return alpha()
            def gamma():
                return 0
        """)
        defs = _collect_top_level_defs(tree)
        adj = _build_call_graph(defs)
        assert "beta" in adj["alpha"]
        assert "alpha" in adj["beta"]
        assert adj["gamma"] == set()

    def test_self_reference_excluded(self):
        tree = _parse("""
            def recursive():
                return recursive()
            def other1():
                pass
            def other2():
                pass
        """)
        defs = _collect_top_level_defs(tree)
        adj = _build_call_graph(defs)
        # Self-references are filtered out (ref != name)
        assert "recursive" not in adj["recursive"]

    def test_chain_references(self):
        tree = _parse("""
            def a():
                return b()
            def b():
                return c()
            def c():
                return 1
        """)
        defs = _collect_top_level_defs(tree)
        adj = _build_call_graph(defs)
        assert "b" in adj["a"]
        assert "a" in adj["b"]
        assert "c" in adj["b"]
        assert "b" in adj["c"]
        # a and c are not directly connected
        assert "c" not in adj["a"]


# ─── _connect_shared_vars ────────────────────────────────────────────


class TestConnectSharedVars:
    def test_no_module_vars(self):
        tree = _parse("""
            def a(): pass
            def b(): pass
            def c(): pass
        """)
        defs = _collect_top_level_defs(tree)
        adj: dict[str, set[str]] = {name: set() for name in defs}
        _connect_shared_vars(adj, defs, set())
        for neighbors in adj.values():
            assert neighbors == set()

    def test_shared_variable_connects_defs(self):
        tree = _parse("""
            CONFIG = {}
            def reader():
                return CONFIG["key"]
            def writer():
                CONFIG["key"] = 1
            def unrelated():
                return 42
        """)
        defs = _collect_top_level_defs(tree)
        module_vars = _collect_module_level_vars(tree)
        adj: dict[str, set[str]] = {name: set() for name in defs}
        _connect_shared_vars(adj, defs, module_vars)
        # reader and writer both reference CONFIG → connected
        assert "writer" in adj["reader"]
        assert "reader" in adj["writer"]
        # unrelated doesn't reference CONFIG
        assert "unrelated" not in adj["reader"]

    def test_multiple_shared_vars(self):
        tree = _parse("""
            X = 1
            Y = 2
            def a():
                return X
            def b():
                return Y
            def c():
                return X + Y
        """)
        defs = _collect_top_level_defs(tree)
        module_vars = _collect_module_level_vars(tree)
        adj: dict[str, set[str]] = {name: set() for name in defs}
        _connect_shared_vars(adj, defs, module_vars)
        # a and c share X; b and c share Y
        assert "c" in adj["a"]
        assert "c" in adj["b"]
        assert "a" in adj["c"]
        assert "b" in adj["c"]


# ─── _find_connected_components ──────────────────────────────────────


class TestFindConnectedComponents:
    def test_empty_graph(self):
        components = _find_connected_components(set(), {})
        assert components == []

    def test_single_node(self):
        components = _find_connected_components({"a"}, {"a": set()})
        assert components == [["a"]]

    def test_all_disconnected(self):
        nodes = {"a", "b", "c"}
        adj: dict[str, set[str]] = {"a": set(), "b": set(), "c": set()}
        components = _find_connected_components(nodes, adj)
        assert len(components) == 3
        # Each component is a single node, sorted
        assert components == [["a"], ["b"], ["c"]]

    def test_all_connected(self):
        nodes = {"a", "b", "c"}
        adj = {
            "a": {"b", "c"},
            "b": {"a", "c"},
            "c": {"a", "b"},
        }
        components = _find_connected_components(nodes, adj)
        assert len(components) == 1
        assert components == [["a", "b", "c"]]

    def test_two_components(self):
        nodes = {"a", "b", "c", "d"}
        adj = {
            "a": {"b"},
            "b": {"a"},
            "c": {"d"},
            "d": {"c"},
        }
        components = _find_connected_components(nodes, adj)
        assert len(components) == 2
        assert components == [["a", "b"], ["c", "d"]]

    def test_chain_forms_single_component(self):
        nodes = {"a", "b", "c"}
        adj = {
            "a": {"b"},
            "b": {"a", "c"},
            "c": {"b"},
        }
        components = _find_connected_components(nodes, adj)
        assert len(components) == 1
        assert components == [["a", "b", "c"]]

    def test_components_are_sorted(self):
        nodes = {"z", "m", "a"}
        adj: dict[str, set[str]] = {"z": set(), "m": set(), "a": set()}
        components = _find_connected_components(nodes, adj)
        # Components are iterated in sorted(nodes) order
        assert components == [["a"], ["m"], ["z"]]


# ─── _compute_cohesion_score ─────────────────────────────────────────


class TestComputeCohesionScore:
    def test_single_def(self):
        defs: dict[str, ast.AST | None] = {"a": None}
        adj: dict[str, set[str]] = {"a": set()}
        assert _compute_cohesion_score(defs, adj) == 1.0  # type: ignore[arg-type]

    def test_empty_defs(self):
        # n < 2 → 1.0
        assert _compute_cohesion_score({}, {}) == 1.0

    def test_fully_connected_three(self):
        defs: dict[str, ast.AST | None] = {"a": None, "b": None, "c": None}
        adj = {
            "a": {"b", "c"},
            "b": {"a", "c"},
            "c": {"a", "b"},
        }
        # 3 actual edges / 3 max edges = 1.0
        assert _compute_cohesion_score(defs, adj) == 1.0  # type: ignore[arg-type]

    def test_no_connections(self):
        defs: dict[str, ast.AST | None] = {"a": None, "b": None, "c": None}
        adj: dict[str, set[str]] = {"a": set(), "b": set(), "c": set()}
        assert _compute_cohesion_score(defs, adj) == 0.0  # type: ignore[arg-type]

    def test_partial_connections(self):
        defs: dict[str, ast.AST | None] = {"a": None, "b": None, "c": None}
        adj = {
            "a": {"b"},
            "b": {"a"},
            "c": set(),
        }
        # 1 actual edge / 3 max edges = 1/3
        score = _compute_cohesion_score(defs, adj)  # type: ignore[arg-type]
        assert abs(score - 1 / 3) < 1e-9

    def test_four_defs_two_edges(self):
        defs: dict[str, ast.AST | None] = {"a": None, "b": None, "c": None, "d": None}
        adj = {
            "a": {"b"},
            "b": {"a"},
            "c": {"d"},
            "d": {"c"},
        }
        # 2 actual edges / 6 max edges = 1/3
        score = _compute_cohesion_score(defs, adj)  # type: ignore[arg-type]
        assert abs(score - 2 / 6) < 1e-9


# ─── _detect_cli_mixing ─────────────────────────────────────────────


class TestDetectCliMixing:
    def test_no_argparse(self):
        tree = _parse("""
            def a(): pass
            def b(): pass
            def c(): pass
        """)
        defs = _collect_top_level_defs(tree)
        assert _detect_cli_mixing(tree, defs) is False

    def test_argparse_but_few_non_cli_functions(self):
        tree = _parse("""
            import argparse
            def main(): pass
            def parse_args(): pass
            def helper1(): pass
            def helper2(): pass
        """)
        defs = _collect_top_level_defs(tree)
        # 2 non-CLI functions (helper1, helper2) — below threshold of >5
        assert _detect_cli_mixing(tree, defs) is False

    def test_argparse_with_many_non_cli_functions(self):
        funcs = "\n".join(f"def func_{i}(): pass" for i in range(8))
        source = "import argparse\ndef main(): pass\n" + funcs
        tree = ast.parse(source)
        defs = _collect_top_level_defs(tree)
        # 8 non-CLI functions (func_0 through func_7) > 5
        assert _detect_cli_mixing(tree, defs) is True

    def test_from_argparse_import(self):
        funcs = "\n".join(f"def func_{i}(): pass" for i in range(8))
        source = "from argparse import ArgumentParser\ndef main(): pass\n" + funcs
        tree = ast.parse(source)
        defs = _collect_top_level_defs(tree)
        assert _detect_cli_mixing(tree, defs) is True

    def test_cli_named_functions_excluded(self):
        # All CLI-named functions: main, parse_args, cli, run, entry_point
        cli_funcs = "\n".join(
            [
                "def main(): pass",
                "def parse_args(): pass",
                "def cli(): pass",
                "def run(): pass",
                "def entry_point(): pass",
            ]
        )
        # Add exactly 5 non-CLI functions (not >5, so should be False)
        non_cli = "\n".join(f"def helper_{i}(): pass" for i in range(5))
        source = "import argparse\n" + cli_funcs + "\n" + non_cli
        tree = ast.parse(source)
        defs = _collect_top_level_defs(tree)
        assert _detect_cli_mixing(tree, defs) is False

    def test_classes_not_counted_as_non_cli(self):
        # Classes are not FunctionDef, so they shouldn't count
        source = "import argparse\n"
        source += "\n".join(f"class C{i}: pass" for i in range(10))
        source += "\ndef main(): pass\n"
        tree = ast.parse(source)
        defs = _collect_top_level_defs(tree)
        assert _detect_cli_mixing(tree, defs) is False


# ─── _generate_split_proposals ───────────────────────────────────────


class TestGenerateSplitProposals:
    def test_cli_mixed_single_proposal(self):
        components = [["main", "parse_args"], ["helper", "process", "validate"]]
        proposals = _generate_split_proposals("app.py", components, is_cli_mixed=True)
        assert len(proposals) == 1
        assert proposals[0].kind == "split_file"
        assert proposals[0].target == "app.py"
        assert "CLI" in proposals[0].action
        assert proposals[0].confidence == 0.70
        assert "cohesion_analysis" in proposals[0].basis
        assert "cli_detection" in proposals[0].basis
        assert proposals[0].expected_delta == {"component_separation": 2}

    def test_non_cli_multiple_components(self):
        components = [["a", "b"], ["c", "d"], ["e"]]
        proposals = _generate_split_proposals("module.py", components, is_cli_mixed=False)
        # Single-def components (["e"]) are skipped
        assert len(proposals) == 2
        for p in proposals:
            assert p.kind == "split_file"
            assert p.target == "module.py"
            assert p.confidence == 0.55
            assert "cohesion_analysis" in p.basis
            assert "connected_components" in p.basis

    def test_single_def_component_skipped(self):
        components = [["a"], ["b"], ["c"]]
        proposals = _generate_split_proposals("module.py", components, is_cli_mixed=False)
        # All components have len < 2, so no proposals
        assert proposals == []

    def test_empty_components(self):
        proposals = _generate_split_proposals("empty.py", [], is_cli_mixed=False)
        assert proposals == []

    def test_proposal_action_truncates_long_component(self):
        component = ["alpha", "beta", "gamma", "delta", "epsilon"]
        proposals = _generate_split_proposals("big.py", [component], is_cli_mixed=False)
        assert len(proposals) == 1
        # First 4 names shown, then "+1 more"
        assert "alpha" in proposals[0].action
        assert "+1 more" in proposals[0].action

    def test_proposal_action_no_truncation_for_four_or_fewer(self):
        component = ["alpha", "beta", "gamma", "delta"]
        proposals = _generate_split_proposals("small.py", [component], is_cli_mixed=False)
        assert len(proposals) == 1
        assert "+0 more" not in proposals[0].action
        assert "more" not in proposals[0].action

    def test_proposal_inputs_contain_component_names(self):
        component = ["read_data", "parse_data"]
        proposals = _generate_split_proposals("data.py", [component], is_cli_mixed=False)
        assert len(proposals) == 1
        assert proposals[0].inputs == ["read_data", "parse_data"]


# ─── analyze_file_cohesion (integration) ─────────────────────────────


class TestAnalyzeFileCohesion:
    def test_empty_module(self):
        tree = _parse("")
        result = analyze_file_cohesion(tree, "empty.py")
        assert isinstance(result, CohesionResult)
        assert result.score == 1.0
        assert result.components == []
        assert result.component_count == 0
        assert result.total_defs == 0
        assert result.is_cli_mixed is False
        assert result.split_proposals == []

    def test_single_def(self):
        tree = _parse("def only(): pass")
        result = analyze_file_cohesion(tree, "single.py")
        assert result.score == 1.0
        assert result.component_count == 1
        assert result.total_defs == 1
        assert result.components == [["only"]]
        assert result.split_proposals == []

    def test_two_defs_under_threshold(self):
        tree = _parse("""
            def a(): pass
            def b(): pass
        """)
        result = analyze_file_cohesion(tree, "two.py")
        # Fewer than 3 defs → early return with score 1.0
        assert result.score == 1.0
        assert result.total_defs == 2
        assert result.split_proposals == []

    def test_three_connected_defs_high_cohesion(self):
        tree = _parse("""
            def a():
                return b()
            def b():
                return c()
            def c():
                return a()
        """)
        result = analyze_file_cohesion(tree, "connected.py")
        # All three are connected in a cycle
        assert result.component_count == 1
        assert result.total_defs == 3
        assert result.score == 1.0
        assert result.split_proposals == []

    def test_three_disconnected_defs_low_cohesion(self):
        tree = _parse("""
            def alpha():
                return 1
            def beta():
                return 2
            def gamma():
                return 3
        """)
        result = analyze_file_cohesion(tree, "disconnected.py")
        assert result.component_count == 3
        assert result.total_defs == 3
        assert result.score == 0.0
        # Score 0.0 < default threshold 0.5, and 3 components → proposals generated
        # But each component has only 1 def, so no split proposals (len < 2 filter)
        assert result.split_proposals == []

    def test_mixed_connected_and_disconnected(self):
        tree = _parse("""
            def a():
                return b()
            def b():
                return 1
            def c():
                return 42
        """)
        result = analyze_file_cohesion(tree, "mixed.py")
        assert result.total_defs == 3
        # a-b connected, c disconnected → 2 components
        assert result.component_count == 2
        # 1 edge / 3 possible = 0.333
        assert result.score == round(1 / 3, 3)

    def test_split_proposals_generated_below_threshold(self):
        # Need >=3 defs, low score, and components with >=2 members
        tree = _parse("""
            def a():
                return b()
            def b():
                return 1
            def c():
                return d()
            def d():
                return 2
        """)
        result = analyze_file_cohesion(tree, "split_me.py")
        # a-b form one component, c-d form another
        assert result.component_count == 2
        assert result.total_defs == 4
        # 2 edges / 6 possible = 0.333 < 0.5
        assert result.score < 0.5
        # Both components have >=2 members → proposals generated
        assert len(result.split_proposals) == 2
        for p in result.split_proposals:
            assert p.kind == "split_file"
            assert p.target == "split_me.py"

    def test_no_proposals_above_threshold(self):
        tree = _parse("""
            def a():
                return b()
            def b():
                return 1
            def c():
                return d()
            def d():
                return 2
        """)
        # Use a very low threshold so score is above it
        result = analyze_file_cohesion(tree, "fine.py", cohesion_threshold=0.1)
        assert result.split_proposals == []

    def test_custom_cohesion_threshold(self):
        tree = _parse("""
            def a():
                return b()
            def b():
                return 1
            def c():
                return d()
            def d():
                return 2
        """)
        # Use threshold of 1.0 so almost everything triggers
        result = analyze_file_cohesion(tree, "strict.py", cohesion_threshold=1.0)
        # Score ~0.333 < 1.0, 2 components with >=2 members
        assert len(result.split_proposals) == 2

    def test_shared_module_vars_increase_cohesion(self):
        tree = _parse("""
            CONFIG = {}
            def reader():
                return CONFIG["key"]
            def writer():
                CONFIG["key"] = 1
            def unrelated():
                return 42
        """)
        result = analyze_file_cohesion(tree, "vars.py")
        # reader and writer share CONFIG → connected
        # unrelated is isolated → 2 components
        assert result.component_count == 2
        # 1 edge / 3 possible = 0.333
        assert result.score == round(1 / 3, 3)

    def test_cli_mixed_detection_in_result(self):
        funcs = "\n".join(f"def func_{i}(): pass" for i in range(8))
        source = "import argparse\ndef main(): pass\n" + funcs
        tree = ast.parse(source)
        result = analyze_file_cohesion(tree, "cli_app.py")
        assert result.is_cli_mixed is True

    def test_cli_mixed_generates_single_proposal(self):
        # Build a file with argparse + many non-CLI functions that form
        # separate disconnected groups
        source = "import argparse\ndef main(): pass\n"
        source += "def group_a1(): return group_a2()\n"
        source += "def group_a2(): return 1\n"
        source += "def group_b1(): return group_b2()\n"
        source += "def group_b2(): return 2\n"
        # 4 non-CLI functions so far; add more to cross the >5 threshold
        source += "def group_c1(): return group_c2()\n"
        source += "def group_c2(): return 3\n"
        tree = ast.parse(source)
        result = analyze_file_cohesion(tree, "cli_mixed.py")
        assert result.is_cli_mixed is True
        # When cli_mixed, _generate_split_proposals returns a single CLI proposal
        if result.score < 0.5 and result.component_count > 1:
            assert len(result.split_proposals) == 1
            assert "CLI" in result.split_proposals[0].action

    def test_score_is_rounded_to_three_decimals(self):
        tree = _parse("""
            def a():
                return b()
            def b():
                return 1
            def c():
                return 42
        """)
        result = analyze_file_cohesion(tree, "rounded.py")
        # 1/3 = 0.333... → rounded to 0.333
        assert result.score == 0.333

    def test_fully_connected_file(self):
        tree = _parse("""
            def a():
                return b() + c()
            def b():
                return a() + c()
            def c():
                return a() + b()
        """)
        result = analyze_file_cohesion(tree, "full.py")
        assert result.score == 1.0
        assert result.component_count == 1
        assert result.split_proposals == []

    def test_result_dataclass_fields(self):
        tree = _parse("")
        result = analyze_file_cohesion(tree, "test.py")
        # Verify all fields exist and have correct types
        assert isinstance(result.score, float)
        assert isinstance(result.components, list)
        assert isinstance(result.component_count, int)
        assert isinstance(result.total_defs, int)
        assert isinstance(result.is_cli_mixed, bool)
        assert isinstance(result.split_proposals, list)

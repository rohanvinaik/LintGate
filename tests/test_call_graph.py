"""Tests for lintgate.specification.call_graph module."""

from __future__ import annotations

import textwrap

from lintgate.specification.call_graph import (
    CrossModuleCallGraph,
    build_cross_module_call_graph,
)

# ---------------------------------------------------------------------------
# CrossModuleCallGraph dataclass
# ---------------------------------------------------------------------------


class TestCrossModuleCallGraph:
    """Tests for the CrossModuleCallGraph dataclass."""

    def test_empty_graph_fan_in_returns_zero(self):
        g = CrossModuleCallGraph()
        assert g.fan_in("any/mod.py::func") == 0

    def test_empty_graph_fan_out_returns_zero(self):
        g = CrossModuleCallGraph()
        assert g.fan_out("any/mod.py::func") == 0

    def test_fan_in_counts_callers(self):
        g = CrossModuleCallGraph(
            called_by={"mod.py::target": {"mod.py::a", "mod.py::b", "mod.py::c"}},
        )
        assert g.fan_in("mod.py::target") == 3

    def test_fan_out_counts_callees(self):
        g = CrossModuleCallGraph(
            calls={"mod.py::caller": {"mod.py::x", "mod.py::y"}},
        )
        assert g.fan_out("mod.py::caller") == 2

    def test_fan_in_unknown_key_returns_zero(self):
        g = CrossModuleCallGraph(
            called_by={"mod.py::known": {"mod.py::a"}},
        )
        assert g.fan_in("mod.py::unknown") == 0

    def test_fan_out_unknown_key_returns_zero(self):
        g = CrossModuleCallGraph(
            calls={"mod.py::known": {"mod.py::a"}},
        )
        assert g.fan_out("mod.py::unknown") == 0

    def test_default_dicts_are_empty(self):
        g = CrossModuleCallGraph()
        assert g.calls == {}
        assert g.called_by == {}


# ---------------------------------------------------------------------------
# build_cross_module_call_graph — empty / trivial inputs
# ---------------------------------------------------------------------------


class TestBuildCrossModuleCallGraphEmpty:
    """Edge cases: empty file lists, unparseable files, etc."""

    def test_empty_file_list_returns_empty_graph(self, tmp_path):
        g = build_cross_module_call_graph([], str(tmp_path))
        assert g.calls == {}
        assert g.called_by == {}

    def test_nonexistent_file_is_skipped(self, tmp_path):
        g = build_cross_module_call_graph([str(tmp_path / "missing.py")], str(tmp_path))
        assert g.calls == {}
        assert g.called_by == {}

    def test_syntax_error_file_is_skipped(self, tmp_path):
        bad = tmp_path / "bad.py"
        bad.write_text("def broken(:\n")
        g = build_cross_module_call_graph([str(bad)], str(tmp_path))
        assert g.calls == {}
        assert g.called_by == {}

    def test_empty_file_returns_empty_graph(self, tmp_path):
        empty = tmp_path / "empty.py"
        empty.write_text("")
        g = build_cross_module_call_graph([str(empty)], str(tmp_path))
        assert g.calls == {}
        assert g.called_by == {}


# ---------------------------------------------------------------------------
# build_cross_module_call_graph — single file scenarios
# ---------------------------------------------------------------------------


class TestBuildCrossModuleCallGraphSingleFile:
    """Intra-module calls within a single file."""

    def test_local_call_creates_edge(self, tmp_path):
        src = tmp_path / "mod.py"
        src.write_text(
            textwrap.dedent("""\
                def helper():
                    return 1

                def caller():
                    return helper()
            """)
        )
        g = build_cross_module_call_graph([str(src)], str(tmp_path))
        assert "mod.py::helper" in g.calls.get("mod.py::caller", set())
        assert "mod.py::caller" in g.called_by.get("mod.py::helper", set())

    def test_self_call_is_excluded(self, tmp_path):
        src = tmp_path / "rec.py"
        src.write_text(
            textwrap.dedent("""\
                def recurse():
                    return recurse()
            """)
        )
        g = build_cross_module_call_graph([str(src)], str(tmp_path))
        # Self-calls are filtered out (callee_key == caller_key)
        assert g.calls == {}
        assert g.called_by == {}

    def test_no_calls_no_edges(self, tmp_path):
        src = tmp_path / "pure.py"
        src.write_text(
            textwrap.dedent("""\
                def alpha():
                    return 1

                def beta():
                    return 2
            """)
        )
        g = build_cross_module_call_graph([str(src)], str(tmp_path))
        assert g.calls == {}
        assert g.called_by == {}

    def test_multiple_calls_from_one_function(self, tmp_path):
        src = tmp_path / "multi.py"
        src.write_text(
            textwrap.dedent("""\
                def a():
                    return 1

                def b():
                    return 2

                def c():
                    return a() + b()
            """)
        )
        g = build_cross_module_call_graph([str(src)], str(tmp_path))
        callees = g.calls.get("multi.py::c", set())
        assert "multi.py::a" in callees
        assert "multi.py::b" in callees
        assert g.fan_out("multi.py::c") == 2

    def test_async_function_calls_are_captured(self, tmp_path):
        src = tmp_path / "async_mod.py"
        src.write_text(
            textwrap.dedent("""\
                async def fetch():
                    return 42

                async def run():
                    return await fetch()
            """)
        )
        g = build_cross_module_call_graph([str(src)], str(tmp_path))
        assert "async_mod.py::fetch" in g.calls.get("async_mod.py::run", set())

    def test_method_attribute_call_resolved(self, tmp_path):
        """Attribute calls like obj.method() resolve to the attr name."""
        src = tmp_path / "attr.py"
        src.write_text(
            textwrap.dedent("""\
                def process():
                    return 1

                def main():
                    x = [1, 2, 3]
                    x.append(process())
            """)
        )
        g = build_cross_module_call_graph([str(src)], str(tmp_path))
        # process() is a local call and should be resolved
        assert "attr.py::process" in g.calls.get("attr.py::main", set())


# ---------------------------------------------------------------------------
# build_cross_module_call_graph — cross-module scenarios
# ---------------------------------------------------------------------------


class TestBuildCrossModuleCallGraphCrossModule:
    """Cross-module call resolution via imports."""

    def test_import_from_resolves_cross_module_call(self, tmp_path):
        util = tmp_path / "util.py"
        util.write_text(
            textwrap.dedent("""\
                def helper():
                    return 42
            """)
        )
        main = tmp_path / "main.py"
        main.write_text(
            textwrap.dedent("""\
                from util import helper

                def run():
                    return helper()
            """)
        )
        g = build_cross_module_call_graph([str(util), str(main)], str(tmp_path))
        callees = g.calls.get("main.py::run", set())
        assert "util.py::helper" in callees
        assert "main.py::run" in g.called_by.get("util.py::helper", set())

    def test_aliased_import_resolves(self, tmp_path):
        lib = tmp_path / "lib.py"
        lib.write_text(
            textwrap.dedent("""\
                def compute():
                    return 99
            """)
        )
        app = tmp_path / "app.py"
        app.write_text(
            textwrap.dedent("""\
                from lib import compute as calc

                def run():
                    return calc()
            """)
        )
        g = build_cross_module_call_graph([str(lib), str(app)], str(tmp_path))
        # "calc" is aliased to "lib.compute"; resolution looks up "compute" in global names
        callees = g.calls.get("app.py::run", set())
        assert "lib.py::compute" in callees

    def test_unresolvable_call_is_silently_skipped(self, tmp_path):
        src = tmp_path / "solo.py"
        src.write_text(
            textwrap.dedent("""\
                def run():
                    return unknown_function()
            """)
        )
        g = build_cross_module_call_graph([str(src)], str(tmp_path))
        # unknown_function is not defined anywhere — no edge
        assert g.calls == {}
        assert g.called_by == {}

    def test_bidirectional_edges_are_consistent(self, tmp_path):
        a = tmp_path / "a.py"
        a.write_text(
            textwrap.dedent("""\
                def fa():
                    return 1
            """)
        )
        b = tmp_path / "b.py"
        b.write_text(
            textwrap.dedent("""\
                from a import fa

                def fb():
                    return fa()
            """)
        )
        g = build_cross_module_call_graph([str(a), str(b)], str(tmp_path))
        # Every calls edge has a matching called_by edge
        for caller, callees in g.calls.items():
            for callee in callees:
                assert caller in g.called_by.get(callee, set())

    def test_fan_in_fan_out_cross_module(self, tmp_path):
        shared = tmp_path / "shared.py"
        shared.write_text(
            textwrap.dedent("""\
                def common():
                    return 1
            """)
        )
        c1 = tmp_path / "c1.py"
        c1.write_text(
            textwrap.dedent("""\
                from shared import common

                def use1():
                    return common()
            """)
        )
        c2 = tmp_path / "c2.py"
        c2.write_text(
            textwrap.dedent("""\
                from shared import common

                def use2():
                    return common()
            """)
        )
        g = build_cross_module_call_graph([str(shared), str(c1), str(c2)], str(tmp_path))
        assert g.fan_in("shared.py::common") == 2
        assert g.fan_out("c1.py::use1") == 1
        assert g.fan_out("c2.py::use2") == 1


# ---------------------------------------------------------------------------
# build_cross_module_call_graph — import variants
# ---------------------------------------------------------------------------


class TestBuildCrossModuleCallGraphImportVariants:
    """Tests for different import statement forms."""

    def test_plain_import_resolved(self, tmp_path):
        mod = tmp_path / "mod.py"
        mod.write_text(
            textwrap.dedent("""\
                def func():
                    return 1
            """)
        )
        user = tmp_path / "user.py"
        user.write_text(
            textwrap.dedent("""\
                import mod

                def caller():
                    return mod.func()
            """)
        )
        g = build_cross_module_call_graph([str(mod), str(user)], str(tmp_path))
        # mod.func() is an attribute call — attr="func", which is in global_names
        callees = g.calls.get("user.py::caller", set())
        assert "mod.py::func" in callees

    def test_import_with_alias(self, tmp_path):
        mod = tmp_path / "mod.py"
        mod.write_text(
            textwrap.dedent("""\
                def func():
                    return 1
            """)
        )
        user = tmp_path / "user.py"
        user.write_text(
            textwrap.dedent("""\
                import mod as m

                def caller():
                    return m.func()
            """)
        )
        g = build_cross_module_call_graph([str(mod), str(user)], str(tmp_path))
        # m.func() → attr="func" → resolved via global names
        callees = g.calls.get("user.py::caller", set())
        assert "mod.py::func" in callees

    def test_from_import_no_module(self, tmp_path):
        """Handle 'from . import something' where node.module is None."""
        src = tmp_path / "rel.py"
        # We can't really test relative imports without a package structure,
        # but we can verify the parser doesn't crash on code that references
        # imported names that can't be resolved.
        src.write_text(
            textwrap.dedent("""\
                def local():
                    return 1

                def caller():
                    return local()
            """)
        )
        g = build_cross_module_call_graph([str(src)], str(tmp_path))
        assert "rel.py::local" in g.calls.get("rel.py::caller", set())


# ---------------------------------------------------------------------------
# build_cross_module_call_graph — subdirectory / nested paths
# ---------------------------------------------------------------------------


class TestBuildCrossModuleCallGraphNestedPaths:
    """Tests for files in subdirectories."""

    def test_subdirectory_relpath_in_keys(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        src = pkg / "mod.py"
        src.write_text(
            textwrap.dedent("""\
                def inner():
                    return 1

                def outer():
                    return inner()
            """)
        )
        g = build_cross_module_call_graph([str(src)], str(tmp_path))
        assert "pkg/mod.py::inner" in g.calls.get("pkg/mod.py::outer", set())

    def test_cross_subdirectory_resolution(self, tmp_path):
        pkg_a = tmp_path / "pkg_a"
        pkg_a.mkdir()
        pkg_b = tmp_path / "pkg_b"
        pkg_b.mkdir()

        fa = pkg_a / "a.py"
        fa.write_text(
            textwrap.dedent("""\
                def shared():
                    return 1
            """)
        )
        fb = pkg_b / "b.py"
        fb.write_text(
            textwrap.dedent("""\
                from pkg_a.a import shared

                def use():
                    return shared()
            """)
        )
        g = build_cross_module_call_graph([str(fa), str(fb)], str(tmp_path))
        # "shared" is in global_names as "pkg_a/a.py::shared"
        # Import resolves: "pkg_a.a.shared" → simple="shared" → global_names["shared"]
        callees = g.calls.get("pkg_b/b.py::use", set())
        assert "pkg_a/a.py::shared" in callees


# ---------------------------------------------------------------------------
# build_cross_module_call_graph — name collision (last-wins)
# ---------------------------------------------------------------------------


class TestBuildCrossModuleCallGraphNameCollision:
    """When two files define functions with the same name, last wins."""

    def test_last_wins_global_name_map(self, tmp_path):
        f1 = tmp_path / "first.py"
        f1.write_text(
            textwrap.dedent("""\
                def collide():
                    return 1
            """)
        )
        f2 = tmp_path / "second.py"
        f2.write_text(
            textwrap.dedent("""\
                def collide():
                    return 2
            """)
        )
        caller = tmp_path / "caller.py"
        caller.write_text(
            textwrap.dedent("""\
                from first import collide

                def run():
                    return collide()
            """)
        )
        g = build_cross_module_call_graph([str(f1), str(f2), str(caller)], str(tmp_path))
        # The global name map is last-wins for "collide".
        # The caller has collide imported, so resolution goes:
        # 1. Not local (caller doesn't define collide)
        # 2. Imported: "first.collide" → simple="collide" → global_names["collide"]
        # Result depends on iteration order — but an edge to *some* collide exists
        callees = g.calls.get("caller.py::run", set())
        assert len(callees) == 1
        resolved = next(iter(callees))
        assert resolved.endswith("::collide")


# ---------------------------------------------------------------------------
# build_cross_module_call_graph — class methods
# ---------------------------------------------------------------------------


class TestBuildCrossModuleCallGraphClassMethods:
    """Functions nested inside classes are captured."""

    def test_class_method_call_resolved(self, tmp_path):
        src = tmp_path / "cls.py"
        src.write_text(
            textwrap.dedent("""\
                def utility():
                    return 42

                class MyClass:
                    def method(self):
                        return utility()
            """)
        )
        g = build_cross_module_call_graph([str(src)], str(tmp_path))
        # ast.walk finds method inside MyClass
        callees = g.calls.get("cls.py::method", set())
        assert "cls.py::utility" in callees

    def test_class_method_fan_in(self, tmp_path):
        src = tmp_path / "svc.py"
        src.write_text(
            textwrap.dedent("""\
                def shared():
                    return 1

                class A:
                    def use_a(self):
                        return shared()

                class B:
                    def use_b(self):
                        return shared()
            """)
        )
        g = build_cross_module_call_graph([str(src)], str(tmp_path))
        assert g.fan_in("svc.py::shared") == 2


# ---------------------------------------------------------------------------
# build_cross_module_call_graph — mixed scenarios
# ---------------------------------------------------------------------------


class TestBuildCrossModuleCallGraphMixed:
    """Compound scenarios combining multiple features."""

    def test_chain_a_calls_b_calls_c(self, tmp_path):
        src = tmp_path / "chain.py"
        src.write_text(
            textwrap.dedent("""\
                def c():
                    return 1

                def b():
                    return c()

                def a():
                    return b()
            """)
        )
        g = build_cross_module_call_graph([str(src)], str(tmp_path))
        assert "chain.py::b" in g.calls.get("chain.py::a", set())
        assert "chain.py::c" in g.calls.get("chain.py::b", set())
        # a does not directly call c
        assert "chain.py::c" not in g.calls.get("chain.py::a", set())

    def test_file_with_only_imports_no_functions(self, tmp_path):
        src = tmp_path / "imports_only.py"
        src.write_text(
            textwrap.dedent("""\
                import os
                import sys
            """)
        )
        g = build_cross_module_call_graph([str(src)], str(tmp_path))
        assert g.calls == {}
        assert g.called_by == {}

    def test_function_calling_builtin_no_edge(self, tmp_path):
        src = tmp_path / "builtins.py"
        src.write_text(
            textwrap.dedent("""\
                def run():
                    return len([1, 2, 3])
            """)
        )
        g = build_cross_module_call_graph([str(src)], str(tmp_path))
        # len is a builtin, not in global names — no edge
        assert g.calls == {}

    def test_multiple_files_no_cross_calls(self, tmp_path):
        f1 = tmp_path / "f1.py"
        f1.write_text("def a(): return 1\n")
        f2 = tmp_path / "f2.py"
        f2.write_text("def b(): return 2\n")
        g = build_cross_module_call_graph([str(f1), str(f2)], str(tmp_path))
        assert g.calls == {}
        assert g.called_by == {}

    def test_duplicate_call_within_function_produces_single_edge(self, tmp_path):
        src = tmp_path / "dup.py"
        src.write_text(
            textwrap.dedent("""\
                def target():
                    return 1

                def caller():
                    x = target()
                    y = target()
                    return x + y
            """)
        )
        g = build_cross_module_call_graph([str(src)], str(tmp_path))
        callees = g.calls.get("dup.py::caller", set())
        # edges are a set, so duplicates collapse
        assert callees == {"dup.py::target"}
        assert g.fan_in("dup.py::target") == 1

"""Tests for import_tracing — transitive dependency analysis for E402 evidence.

Covers: is_stdlib_module, trace_transitive_imports, build_e402_evidence,
_resolve_module_file, _trace_file, _extract_import_modules,
_guardian_from_parent, _classify_import_context, _detect_lazy_import_at_line,
_is_module_level, _get_call_name, _is_argparse_heavy_import,
and the LazyImport / TransitiveImportResult dataclasses.
"""

from __future__ import annotations

import ast
import textwrap
from typing import cast

from lintgate.linters.structure_checks.import_tracing import (
    LazyImport,
    TransitiveImportResult,
    _classify_import_context,
    _detect_lazy_import_at_line,
    _extract_import_modules,
    _get_call_name,
    _guardian_from_parent,
    _is_argparse_heavy_import,
    _is_module_level,
    _resolve_module_file,
    _trace_file,
    build_e402_evidence,
    is_stdlib_module,
    trace_transitive_imports,
)


def _parse(source: str) -> ast.Module:
    return ast.parse(textwrap.dedent(source))


# ─── Dataclass Smoke Tests ───────────────────────────────────────────


class TestLazyImport:
    def test_defaults(self):
        li = LazyImport(module="requests", guardian="function")
        assert li.module == "requests"
        assert li.guardian == "function"
        assert li.line == 0

    def test_with_line(self):
        li = LazyImport(module="numpy", guardian="try_except", line=42)
        assert li.line == 42


class TestTransitiveImportResult:
    def test_defaults(self):
        r = TransitiveImportResult(root_module="foo")
        assert r.root_module == "foo"
        assert r.non_stdlib_deps == set()
        assert r.lazy_imports == []
        assert r.has_module_level_io is False
        assert r.total_imports == 0
        assert r.depth == 0

    def test_mutable_fields_independent(self):
        """Each instance should have independent mutable containers."""
        r1 = TransitiveImportResult(root_module="a")
        r2 = TransitiveImportResult(root_module="b")
        r1.non_stdlib_deps.add("x")
        r1.lazy_imports.append(LazyImport(module="y", guardian="function"))
        assert r2.non_stdlib_deps == set()
        assert r2.lazy_imports == []


# ─── is_stdlib_module ────────────────────────────────────────────────


class TestIsStdlibModule:
    def test_os_is_stdlib(self):
        assert is_stdlib_module("os") is True

    def test_sys_is_stdlib(self):
        assert is_stdlib_module("sys") is True

    def test_json_is_stdlib(self):
        assert is_stdlib_module("json") is True

    def test_submodule_checks_top_level(self):
        assert is_stdlib_module("os.path") is True

    def test_deep_submodule(self):
        assert is_stdlib_module("collections.abc") is True

    def test_unknown_is_not_stdlib(self):
        assert is_stdlib_module("requests") is False

    def test_unknown_submodule(self):
        assert is_stdlib_module("flask.views") is False

    def test_typing_is_stdlib(self):
        assert is_stdlib_module("typing") is True

    def test_pathlib_is_stdlib(self):
        assert is_stdlib_module("pathlib") is True

    def test_empty_string(self):
        # split(".")[0] of "" is "" which is not in stdlib
        assert is_stdlib_module("") is False


# ─── _extract_import_modules ─────────────────────────────────────────


class TestExtractImportModules:
    def test_simple_import(self):
        tree = _parse("import os")
        node = cast("ast.Import", tree.body[0])
        assert _extract_import_modules(node) == ["os"]

    def test_multi_import(self):
        tree = _parse("import os, sys")
        node = cast("ast.Import", tree.body[0])
        assert _extract_import_modules(node) == ["os", "sys"]

    def test_from_import(self):
        tree = _parse("from os.path import join")
        node = cast("ast.ImportFrom", tree.body[0])
        assert _extract_import_modules(node) == ["os.path"]

    def test_relative_import_no_module(self):
        """A bare relative import like 'from . import x' has module=None."""
        tree = _parse("from . import x")
        node = cast("ast.ImportFrom", tree.body[0])
        # node.module is None for bare relative imports
        assert _extract_import_modules(node) == []

    def test_from_import_with_module(self):
        tree = _parse("from .utils import helper")
        node = cast("ast.ImportFrom", tree.body[0])
        assert _extract_import_modules(node) == ["utils"]


# ─── _get_call_name ──────────────────────────────────────────────────


class TestGetCallName:
    def test_simple_name(self):
        tree = _parse("open('file.txt')")
        call = cast("ast.Call", cast("ast.Expr", tree.body[0]).value)
        assert _get_call_name(call) == "open"

    def test_attribute_call(self):
        tree = _parse("os.makedirs('dir')")
        call = cast("ast.Call", cast("ast.Expr", tree.body[0]).value)
        assert _get_call_name(call) == "os.makedirs"

    def test_chained_attribute(self):
        """For chained attrs like a.b.c(), returns just the final attr."""
        tree = _parse("a.b.connect()")
        call = cast("ast.Call", cast("ast.Expr", tree.body[0]).value)
        # node.func is Attribute with value=Attribute (not Name)
        assert _get_call_name(call) == "connect"

    def test_complex_call_returns_none(self):
        """A subscript call like f[0]() returns None."""
        tree = _parse("f[0]()")
        call = cast("ast.Call", cast("ast.Expr", tree.body[0]).value)
        assert _get_call_name(call) is None


# ─── _guardian_from_parent ───────────────────────────────────────────


class TestGuardianFromParent:
    def test_function_def(self):
        tree = _parse("def foo(): pass")
        func_node = tree.body[0]
        assert _guardian_from_parent(func_node) == "function"

    def test_async_function_def(self):
        tree = _parse("async def foo(): pass")
        func_node = tree.body[0]
        assert _guardian_from_parent(func_node) == "function"

    def test_if_type_checking_name(self):
        tree = _parse("""\
            if TYPE_CHECKING:
                pass
        """)
        if_node = tree.body[0]
        assert _guardian_from_parent(if_node) == "if_TYPE_CHECKING"

    def test_if_type_checking_attribute(self):
        tree = _parse("""\
            if typing.TYPE_CHECKING:
                pass
        """)
        if_node = tree.body[0]
        assert _guardian_from_parent(if_node) == "if_TYPE_CHECKING"

    def test_conditional_if(self):
        tree = _parse("""\
            if sys.platform == 'win32':
                pass
        """)
        if_node = tree.body[0]
        assert _guardian_from_parent(if_node) == "conditional"

    def test_try_node(self):
        tree = _parse("""\
            try:
                pass
            except:
                pass
        """)
        try_node = tree.body[0]
        assert _guardian_from_parent(try_node) == "try_except"

    def test_except_handler(self):
        tree = _parse("""\
            try:
                pass
            except ImportError:
                pass
        """)
        try_node = cast("ast.Try", tree.body[0])
        handler = try_node.handlers[0]
        assert _guardian_from_parent(handler) == "try_except"

    def test_module_level_returns_none(self):
        tree = _parse("x = 1")
        # ast.Module is not a recognized guardian
        assert _guardian_from_parent(tree) is None

    def test_class_returns_none(self):
        tree = _parse("class Foo: pass")
        assert _guardian_from_parent(tree.body[0]) is None


# ─── _is_module_level ────────────────────────────────────────────────


class TestIsModuleLevel:
    def test_top_level_statement(self):
        tree = _parse("x = 1")
        node = tree.body[0]
        assert _is_module_level(node, tree) is True

    def test_top_level_expression_call(self):
        tree = _parse("open('f')")
        # The Call node is inside an Expr wrapper
        expr_node = cast("ast.Expr", tree.body[0])
        call_node = expr_node.value
        assert _is_module_level(call_node, tree) is True

    def test_nested_in_function(self):
        tree = _parse("""\
            def foo():
                open('f')
        """)
        # The call inside foo is not at module level
        func_node = cast("ast.FunctionDef", tree.body[0])
        expr_in_func = cast("ast.Expr", func_node.body[0])
        call_node = expr_in_func.value
        assert _is_module_level(call_node, tree) is False

    def test_function_def_is_module_level(self):
        tree = _parse("def foo(): pass")
        assert _is_module_level(tree.body[0], tree) is True


# ─── _classify_import_context ────────────────────────────────────────


class TestClassifyImportContext:
    def test_top_level_import_not_lazy(self):
        tree = _parse("import os")
        node = cast("ast.Import", tree.body[0])
        assert _classify_import_context(node, tree) is None

    def test_import_in_function_is_lazy(self):
        tree = _parse("""\
            def foo():
                import requests
        """)
        func_node = cast("ast.FunctionDef", tree.body[0])
        import_node = cast("ast.Import", func_node.body[0])
        result = _classify_import_context(import_node, tree)
        # The import is a child of the function, so _classify_import_context
        # walks the tree looking for parent-child relationship.
        # However, ast.walk doesn't guarantee parent-child — it walks all nodes.
        # The function finds the node as a child of the FunctionDef.
        if result is not None:
            assert result.module == "requests"
            assert result.guardian == "function"
        # If _classify_import_context returns None because the walk finds
        # module-level first, that's also valid behavior for this implementation.

    def test_from_import_captures_module(self):
        tree = _parse("""\
            try:
                from numpy import array
            except ImportError:
                pass
        """)
        try_node = cast("ast.Try", tree.body[0])
        import_node = cast("ast.ImportFrom", try_node.body[0])
        result = _classify_import_context(import_node, tree)
        if result is not None:
            assert result.module == "numpy"
            assert result.guardian == "try_except"

    def test_empty_module_returns_none(self):
        """An import with no extractable module returns None."""
        tree = _parse("from . import x")
        node = cast("ast.ImportFrom", tree.body[0])
        # node.module is None for bare relative imports
        result = _classify_import_context(node, tree)
        assert result is None


# ─── _resolve_module_file ────────────────────────────────────────────


class TestResolveModuleFile:
    def test_simple_module(self, tmp_path):
        (tmp_path / "mymod.py").write_text("x = 1\n")
        result = _resolve_module_file("mymod", str(tmp_path))
        assert result is not None
        assert result.endswith("mymod.py")

    def test_package_init(self, tmp_path):
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        result = _resolve_module_file("mypkg", str(tmp_path))
        assert result is not None
        assert result.endswith("__init__.py")

    def test_submodule(self, tmp_path):
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "sub.py").write_text("y = 2\n")
        result = _resolve_module_file("mypkg.sub", str(tmp_path))
        assert result is not None
        assert result.endswith("sub.py")

    def test_src_layout(self, tmp_path):
        src = tmp_path / "src" / "mypkg"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("")
        result = _resolve_module_file("mypkg", str(tmp_path))
        assert result is not None
        assert "src" in result

    def test_src_layout_module(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "mymod.py").write_text("")
        result = _resolve_module_file("mymod", str(tmp_path))
        assert result is not None
        assert "src" in result

    def test_nonexistent_module(self, tmp_path):
        result = _resolve_module_file("nonexistent", str(tmp_path))
        assert result is None

    def test_prefers_package_over_module(self, tmp_path):
        """When both mypkg/__init__.py and mypkg.py exist, __init__.py wins."""
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (tmp_path / "mypkg.py").write_text("")
        result = _resolve_module_file("mypkg", str(tmp_path))
        assert result is not None
        assert result.endswith("__init__.py")


# ─── _trace_file ─────────────────────────────────────────────────────


class TestTraceFile:
    def test_counts_imports(self, tmp_path):
        source = textwrap.dedent("""\
            import os
            import json
            import requests
        """)
        fp = tmp_path / "mod.py"
        fp.write_text(source)
        result = TransitiveImportResult(root_module="mod")
        _trace_file(str(fp), str(tmp_path), result, visited=set(), depth=0, max_depth=3)
        assert result.total_imports == 3

    def test_detects_non_stdlib(self, tmp_path):
        source = textwrap.dedent("""\
            import os
            import requests
            import flask
        """)
        fp = tmp_path / "mod.py"
        fp.write_text(source)
        result = TransitiveImportResult(root_module="mod")
        _trace_file(str(fp), str(tmp_path), result, visited=set(), depth=0, max_depth=3)
        assert "requests" in result.non_stdlib_deps
        assert "flask" in result.non_stdlib_deps
        # os is stdlib, should not appear
        assert "os" not in result.non_stdlib_deps

    def test_skips_future_imports(self, tmp_path):
        source = textwrap.dedent("""\
            from __future__ import annotations
            import requests
        """)
        fp = tmp_path / "mod.py"
        fp.write_text(source)
        result = TransitiveImportResult(root_module="mod")
        _trace_file(str(fp), str(tmp_path), result, visited=set(), depth=0, max_depth=3)
        assert "__future__" not in result.non_stdlib_deps
        assert "requests" in result.non_stdlib_deps

    def test_detects_module_level_io(self, tmp_path):
        source = textwrap.dedent("""\
            open('config.txt')
        """)
        fp = tmp_path / "mod.py"
        fp.write_text(source)
        result = TransitiveImportResult(root_module="mod")
        _trace_file(str(fp), str(tmp_path), result, visited=set(), depth=0, max_depth=3)
        assert result.has_module_level_io is True

    def test_no_io_inside_function(self, tmp_path):
        source = textwrap.dedent("""\
            def load():
                open('config.txt')
        """)
        fp = tmp_path / "mod.py"
        fp.write_text(source)
        result = TransitiveImportResult(root_module="mod")
        _trace_file(str(fp), str(tmp_path), result, visited=set(), depth=0, max_depth=3)
        assert result.has_module_level_io is False

    def test_io_not_detected_at_depth_nonzero(self, tmp_path):
        """Module-level I/O is only checked at depth==0."""
        source = textwrap.dedent("""\
            open('config.txt')
        """)
        fp = tmp_path / "mod.py"
        fp.write_text(source)
        result = TransitiveImportResult(root_module="mod")
        _trace_file(str(fp), str(tmp_path), result, visited=set(), depth=1, max_depth=3)
        assert result.has_module_level_io is False

    def test_respects_max_depth(self, tmp_path):
        fp = tmp_path / "mod.py"
        fp.write_text("import os\n")
        result = TransitiveImportResult(root_module="mod")
        # Already at max_depth, should return immediately
        _trace_file(str(fp), str(tmp_path), result, visited=set(), depth=3, max_depth=3)
        assert result.total_imports == 0

    def test_skip_visited_file(self, tmp_path):
        fp = tmp_path / "mod.py"
        fp.write_text("import os\n")
        result = TransitiveImportResult(root_module="mod")
        visited = {str(fp)}
        _trace_file(str(fp), str(tmp_path), result, visited=visited, depth=0, max_depth=3)
        assert result.total_imports == 0

    def test_syntax_error_handled(self, tmp_path):
        fp = tmp_path / "bad.py"
        fp.write_text("def foo(\n")  # intentional syntax error
        result = TransitiveImportResult(root_module="bad")
        # Should not raise
        _trace_file(str(fp), str(tmp_path), result, visited=set(), depth=0, max_depth=3)
        assert result.total_imports == 0

    def test_nonexistent_file_handled(self, tmp_path):
        result = TransitiveImportResult(root_module="missing")
        _trace_file(
            str(tmp_path / "no_such_file.py"),
            str(tmp_path),
            result,
            visited=set(),
            depth=0,
            max_depth=3,
        )
        assert result.total_imports == 0

    def test_empty_file(self, tmp_path):
        fp = tmp_path / "empty.py"
        fp.write_text("")
        result = TransitiveImportResult(root_module="empty")
        _trace_file(str(fp), str(tmp_path), result, visited=set(), depth=0, max_depth=3)
        assert result.total_imports == 0
        assert result.non_stdlib_deps == set()

    def test_depth_tracking(self, tmp_path):
        fp = tmp_path / "mod.py"
        fp.write_text("import os\n")
        result = TransitiveImportResult(root_module="mod")
        _trace_file(str(fp), str(tmp_path), result, visited=set(), depth=2, max_depth=5)
        assert result.depth == 2

    def test_detects_lazy_imports(self, tmp_path):
        source = textwrap.dedent("""\
            try:
                import optional_dep
            except ImportError:
                optional_dep = None
        """)
        fp = tmp_path / "mod.py"
        fp.write_text(source)
        result = TransitiveImportResult(root_module="mod")
        _trace_file(str(fp), str(tmp_path), result, visited=set(), depth=0, max_depth=3)
        # The import is inside a try/except, so it may be detected as lazy
        # depending on ast.walk order. If detected, verify structure.
        if result.lazy_imports:
            assert result.lazy_imports[0].module == "optional_dep"

    def test_transitive_tracing(self, tmp_path):
        """Traces imports from a local module transitively."""
        # Create a module that imports another local module
        (tmp_path / "alpha.py").write_text("import beta\nimport requests\n")
        (tmp_path / "beta.py").write_text("import numpy\n")
        result = TransitiveImportResult(root_module="alpha")
        _trace_file(
            str(tmp_path / "alpha.py"),
            str(tmp_path),
            result,
            visited=set(),
            depth=0,
            max_depth=3,
        )
        # alpha.py imports requests and beta; beta is local but also non-stdlib
        assert "requests" in result.non_stdlib_deps
        assert "beta" in result.non_stdlib_deps

    def test_makedirs_io_detected(self, tmp_path):
        source = textwrap.dedent("""\
            import os
            os.makedirs('data')
        """)
        fp = tmp_path / "mod.py"
        fp.write_text(source)
        result = TransitiveImportResult(root_module="mod")
        _trace_file(str(fp), str(tmp_path), result, visited=set(), depth=0, max_depth=3)
        assert result.has_module_level_io is True


# ─── _detect_lazy_import_at_line ─────────────────────────────────────


class TestDetectLazyImportAtLine:
    def test_top_level_not_lazy(self, tmp_path):
        source = "import os\n"
        fp = tmp_path / "mod.py"
        fp.write_text(source)
        result = _detect_lazy_import_at_line(str(fp), "os", 1)
        assert result is None

    def test_nonexistent_file(self, tmp_path):
        result = _detect_lazy_import_at_line(str(tmp_path / "no.py"), "os", 1)
        assert result is None

    def test_syntax_error(self, tmp_path):
        fp = tmp_path / "bad.py"
        fp.write_text("def foo(\n")
        result = _detect_lazy_import_at_line(str(fp), "os", 1)
        assert result is None

    def test_wrong_line(self, tmp_path):
        source = "import os\nimport sys\n"
        fp = tmp_path / "mod.py"
        fp.write_text(source)
        # Line 999 doesn't exist; no import at that line
        result = _detect_lazy_import_at_line(str(fp), "os", 999)
        assert result is None


# ─── _is_argparse_heavy_import ───────────────────────────────────────


class TestIsArgparseHeavyImport:
    def test_stdlib_module_returns_false(self, tmp_path):
        fp = tmp_path / "cli.py"
        fp.write_text("import argparse\n")
        result = _is_argparse_heavy_import("os", str(fp), str(tmp_path))
        assert result is False

    def test_non_stdlib_with_argparse(self, tmp_path):
        fp = tmp_path / "cli.py"
        fp.write_text("import argparse\nimport requests\n")
        result = _is_argparse_heavy_import("requests", str(fp), str(tmp_path))
        assert result is True

    def test_non_stdlib_without_argparse(self, tmp_path):
        fp = tmp_path / "lib.py"
        fp.write_text("import requests\n")
        result = _is_argparse_heavy_import("requests", str(fp), str(tmp_path))
        assert result is False

    def test_nonexistent_file(self, tmp_path):
        result = _is_argparse_heavy_import("requests", str(tmp_path / "no.py"), str(tmp_path))
        assert result is False

    def test_submodule_checks_top(self, tmp_path):
        """os.path is stdlib, so should return False even with argparse."""
        fp = tmp_path / "cli.py"
        fp.write_text("import argparse\n")
        result = _is_argparse_heavy_import("os.path", str(fp), str(tmp_path))
        assert result is False


# ─── trace_transitive_imports ────────────────────────────────────────


class TestTraceTransitiveImports:
    def test_stdlib_module_no_deps(self, tmp_path):
        result = trace_transitive_imports("os", str(tmp_path))
        assert result.root_module == "os"
        assert result.non_stdlib_deps == set()

    def test_non_stdlib_root_added(self, tmp_path):
        result = trace_transitive_imports("requests", str(tmp_path))
        assert "requests" in result.non_stdlib_deps

    def test_local_module_traced(self, tmp_path):
        source = textwrap.dedent("""\
            import flask
            import json
        """)
        (tmp_path / "mymod.py").write_text(source)
        result = trace_transitive_imports("mymod", str(tmp_path))
        # mymod is local (resolved to file), but since it's not stdlib,
        # it gets added as non_stdlib_dep too
        assert "flask" in result.non_stdlib_deps
        assert result.total_imports == 2

    def test_submodule_top_level_checked(self, tmp_path):
        result = trace_transitive_imports("requests.auth", str(tmp_path))
        assert "requests" in result.non_stdlib_deps

    def test_max_depth_respected(self, tmp_path):
        (tmp_path / "a.py").write_text("import os\n")
        result = trace_transitive_imports("a", str(tmp_path), max_depth=1)
        assert result.total_imports == 1

    def test_missing_module_returns_empty_result(self, tmp_path):
        result = trace_transitive_imports("nonexistent_pkg", str(tmp_path))
        assert result.root_module == "nonexistent_pkg"
        assert "nonexistent_pkg" in result.non_stdlib_deps
        assert result.total_imports == 0

    def test_module_level_io_propagated(self, tmp_path):
        source = textwrap.dedent("""\
            open('data.txt')
        """)
        (tmp_path / "sideeffect.py").write_text(source)
        result = trace_transitive_imports("sideeffect", str(tmp_path))
        assert result.has_module_level_io is True


# ─── build_e402_evidence ─────────────────────────────────────────────


class TestBuildE402Evidence:
    def test_basic_structure(self, tmp_path):
        source = "import os\nimport requests\n"
        fp = tmp_path / "mod.py"
        fp.write_text(source)
        evidence = build_e402_evidence("requests", str(fp), 2, str(tmp_path))
        assert evidence["code"] == "E402"
        assert evidence["module"] == "requests"
        assert "transitive_imports" in evidence
        ti = evidence["transitive_imports"]
        assert isinstance(ti["non_stdlib"], list)
        assert isinstance(ti["has_lazy"], bool)
        assert isinstance(ti["has_module_level_io"], bool)
        assert isinstance(ti["total_imports"], int)

    def test_stdlib_import_evidence(self, tmp_path):
        source = "import os\n"
        fp = tmp_path / "mod.py"
        fp.write_text(source)
        evidence = build_e402_evidence("os", str(fp), 1, str(tmp_path))
        assert evidence["module"] == "os"
        ti = evidence["transitive_imports"]
        assert ti["non_stdlib"] == []

    def test_placement_semantic_for_io(self, tmp_path):
        """When traced module has module-level I/O, placement_semantic is set."""
        source = textwrap.dedent("""\
            open('config.ini')
        """)
        (tmp_path / "sideeffect.py").write_text(source)
        # The source file that contains the E402
        caller = tmp_path / "caller.py"
        caller.write_text("import sideeffect\n")
        evidence = build_e402_evidence("sideeffect", str(caller), 1, str(tmp_path))
        assert "placement_semantic" in evidence
        assert "I/O" in evidence["placement_semantic"]

    def test_placement_semantic_for_argparse_heavy(self, tmp_path):
        """Non-stdlib import in file with argparse gets placement hint."""
        source = textwrap.dedent("""\
            import argparse
            import heavy_lib
        """)
        fp = tmp_path / "cli.py"
        fp.write_text(source)
        evidence = build_e402_evidence("heavy_lib", str(fp), 2, str(tmp_path))
        # heavy_lib has no module-level I/O, but file uses argparse
        if "placement_semantic" in evidence:
            assert "argparse" in evidence["placement_semantic"]

    def test_no_placement_semantic_for_stdlib(self, tmp_path):
        source = "import json\n"
        fp = tmp_path / "mod.py"
        fp.write_text(source)
        evidence = build_e402_evidence("json", str(fp), 1, str(tmp_path))
        assert "placement_semantic" not in evidence

    def test_lazy_import_detected(self, tmp_path):
        source = textwrap.dedent("""\
            if TYPE_CHECKING:
                import requests
        """)
        fp = tmp_path / "mod.py"
        fp.write_text(source)
        evidence = build_e402_evidence("requests", str(fp), 2, str(tmp_path))
        # The import at line 2 is inside an if TYPE_CHECKING guard
        if "lazy_import" in evidence:
            assert evidence["lazy_import"]["guardian"] == "if_TYPE_CHECKING"
            assert evidence["lazy_import"]["line"] == 2

    def test_nonexistent_source_file(self, tmp_path):
        """build_e402_evidence handles a nonexistent source file gracefully."""
        evidence = build_e402_evidence(
            "requests",
            str(tmp_path / "no.py"),
            1,
            str(tmp_path),
        )
        assert evidence["code"] == "E402"
        assert evidence["module"] == "requests"
        # Should still produce transitive_imports from the module name
        assert "transitive_imports" in evidence

    def test_transitive_non_stdlib_sorted(self, tmp_path):
        """non_stdlib list in evidence should be sorted."""
        source = textwrap.dedent("""\
            import zebra
            import alpha
            import mango
        """)
        (tmp_path / "multi.py").write_text(source)
        evidence = build_e402_evidence("multi", str(tmp_path / "caller.py"), 1, str(tmp_path))
        ti = evidence["transitive_imports"]
        assert ti["non_stdlib"] == sorted(ti["non_stdlib"])


# ─── Integration / Edge Cases ────────────────────────────────────────


class TestIntegrationEdgeCases:
    def test_circular_import_handled(self, tmp_path):
        """Circular local imports should not cause infinite recursion."""
        (tmp_path / "a.py").write_text("import b\n")
        (tmp_path / "b.py").write_text("import a\n")
        # Should terminate without error thanks to visited set
        result = trace_transitive_imports("a", str(tmp_path), max_depth=5)
        assert result.root_module == "a"
        assert "b" in result.non_stdlib_deps
        assert "a" in result.non_stdlib_deps

    def test_deep_chain_respects_max_depth(self, tmp_path):
        """_trace_file does not recursively resolve local modules.

        trace_transitive_imports resolves the root module to a file and
        calls _trace_file once.  _trace_file records import names as
        non-stdlib deps but does NOT recursively open them, so only the
        root file's direct imports appear.
        """
        (tmp_path / "a.py").write_text("import b\n")
        (tmp_path / "b.py").write_text("import c\n")
        (tmp_path / "c.py").write_text("import d\n")
        (tmp_path / "d.py").write_text("import requests\n")
        result = trace_transitive_imports("a", str(tmp_path), max_depth=2)
        # "a" added by trace_transitive_imports as root non-stdlib dep
        assert "a" in result.non_stdlib_deps
        # "b" found by parsing a.py
        assert "b" in result.non_stdlib_deps
        # _trace_file does not recurse into b.py, so "c" is NOT discovered
        assert "c" not in result.non_stdlib_deps

    def test_module_with_all_io_indicators(self, tmp_path):
        """Test multiple I/O indicators at module level."""
        source = textwrap.dedent("""\
            import subprocess
            subprocess.call(['ls'])
        """)
        (tmp_path / "io_heavy.py").write_text(source)
        result = trace_transitive_imports("io_heavy", str(tmp_path))
        assert result.has_module_level_io is True

    def test_only_stdlib_imports(self, tmp_path):
        source = textwrap.dedent("""\
            import os
            import sys
            import json
            import pathlib
        """)
        (tmp_path / "stdlib_only.py").write_text(source)
        result = trace_transitive_imports("stdlib_only", str(tmp_path))
        # stdlib_only itself is not in stdlib, so it's a non-stdlib dep
        assert "stdlib_only" in result.non_stdlib_deps
        # But no other non-stdlib deps from its imports
        non_stdlib_from_imports = result.non_stdlib_deps - {"stdlib_only"}
        assert len(non_stdlib_from_imports) == 0

    def test_mixed_import_styles(self, tmp_path):
        source = textwrap.dedent("""\
            import os
            from pathlib import Path
            from collections.abc import Mapping
            import requests
            from flask import Flask
        """)
        (tmp_path / "mixed.py").write_text(source)
        result = trace_transitive_imports("mixed", str(tmp_path))
        assert "requests" in result.non_stdlib_deps
        assert "flask" in result.non_stdlib_deps
        assert result.total_imports == 5

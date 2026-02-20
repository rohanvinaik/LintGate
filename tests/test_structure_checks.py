"""Per-check tests for structure checker sub-modules.

Covers: _helpers, file_checks, function_checks, class_checks,
and the StructureChecker thin-orchestrator integration.
"""

from __future__ import annotations

import ast
import textwrap

from lintgate.linters.structure_checker import StructureChecker
from lintgate.linters.structure_checks._helpers import (
    collect_class_attributes,
    count_local_names,
)
from lintgate.linters.structure_checks.class_checks import (
    check_class,
    check_class_attributes,
    check_class_methods,
    check_class_parents,
)
from lintgate.linters.structure_checks.file_checks import (
    check_file_size,
    check_file_structure,
)
from lintgate.linters.structure_checks.function_checks import (
    check_cognitive_complexity,
    check_function,
    check_function_args,
    check_function_locals,
    check_function_returns,
    check_function_statements,
    check_nesting_depth,
)
from lintgate.types import LinterContext

# Default thresholds matching StructureChecker._DEFAULTS
_DEFAULTS = {
    "max_file_lines": 500,
    "max_file_classes": 5,
    "max_file_functions": 20,
    "max_function_args": 7,
    "max_function_locals": 15,
    "max_function_statements": 50,
    "max_function_returns": 6,
    "max_nesting_depth": 4,
    "cognitive_complexity_threshold": 15,
    "max_class_attributes": 10,
    "max_class_methods": 20,
    "max_class_parents": 4,
}


def _parse(source: str) -> ast.Module:
    return ast.parse(textwrap.dedent(source))


def _parse_func(source: str) -> ast.FunctionDef:
    tree = _parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    msg = "No function found in source"
    raise ValueError(msg)


def _parse_class(source: str) -> ast.ClassDef:
    tree = _parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            return node
    msg = "No class found in source"
    raise ValueError(msg)


# ─── Helpers ──────────────────────────────────────────────────────────


class TestHelpers:
    def test_collect_class_attributes_self(self):
        node = _parse_class("""
            class Foo:
                def __init__(self):
                    self.x = 1
                    self.y = 2
        """)
        attrs = collect_class_attributes(node)
        assert attrs == {"x", "y"}

    def test_collect_class_attributes_class_level(self):
        node = _parse_class("""
            class Foo:
                bar = 10
                baz: int = 20
        """)
        attrs = collect_class_attributes(node)
        assert attrs == {"bar", "baz"}

    def test_collect_class_attributes_combined(self):
        node = _parse_class("""
            class Foo:
                class_var = 1
                def __init__(self):
                    self.instance_var = 2
        """)
        attrs = collect_class_attributes(node)
        assert attrs == {"class_var", "instance_var"}

    def test_count_local_names_basic(self):
        func = _parse_func("""
            def f():
                x = 1
                y = 2
                z = x + y
        """)
        names = count_local_names(func)
        assert names == {"x", "y", "z"}

    def test_count_local_names_tuple_unpack(self):
        func = _parse_func("""
            def f():
                a, b = 1, 2
        """)
        names = count_local_names(func)
        assert names == {"a", "b"}

    def test_count_local_names_for_loop(self):
        func = _parse_func("""
            def f():
                for i in range(10):
                    pass
        """)
        names = count_local_names(func)
        assert "i" in names

    def test_count_local_names_with_statement(self):
        func = _parse_func("""
            def f():
                with open("x") as fh:
                    pass
        """)
        names = count_local_names(func)
        assert "fh" in names


# ─── File Checks ─────────────────────────────────────────────────────


class TestFileChecks:
    def test_file_size_under_limit(self):
        lines = ["x\n"] * 100
        issues = list(check_file_size("test.py", lines, _DEFAULTS))
        assert len(issues) == 0

    def test_file_size_over_limit_warning(self):
        lines = ["x\n"] * 600
        issues = list(check_file_size("test.py", lines, _DEFAULTS))
        assert len(issues) == 1
        assert issues[0].kind == "file-too-long"
        assert issues[0].severity == "warning"

    def test_file_size_over_double_blocking(self):
        lines = ["x\n"] * 1200
        issues = list(check_file_size("test.py", lines, _DEFAULTS))
        assert len(issues) == 1
        assert issues[0].severity == "blocking"

    def test_file_structure_too_many_classes(self):
        source = "\n".join(f"class C{i}: pass" for i in range(8))
        tree = ast.parse(source)
        issues = list(check_file_structure("test.py", tree, _DEFAULTS))
        kinds = {i.kind for i in issues}
        assert "too-many-classes" in kinds

    def test_file_structure_too_many_functions(self):
        source = "\n".join(f"def f{i}(): pass" for i in range(25))
        tree = ast.parse(source)
        issues = list(check_file_structure("test.py", tree, _DEFAULTS))
        kinds = {i.kind for i in issues}
        assert "too-many-functions" in kinds

    def test_file_structure_under_limit(self):
        tree = ast.parse("class A: pass\ndef f(): pass\n")
        issues = list(check_file_structure("test.py", tree, _DEFAULTS))
        assert len(issues) == 0


# ─── Function Checks ─────────────────────────────────────────────────


class TestFunctionArgs:
    def test_under_limit(self):
        func = _parse_func("def f(a, b, c): pass")
        issues = list(check_function_args("test.py", func, "f", _DEFAULTS))
        assert len(issues) == 0

    def test_self_excluded(self):
        func = _parse_func("def f(self, a, b, c, d, e, f, g): pass")
        issues = list(check_function_args("test.py", func, "f", _DEFAULTS))
        assert len(issues) == 0

    def test_over_limit_warning(self):
        func = _parse_func("def f(a, b, c, d, e, f, g, h): pass")
        issues = list(check_function_args("test.py", func, "f", _DEFAULTS))
        assert len(issues) == 1
        assert issues[0].kind == "too-many-args"
        assert issues[0].severity == "warning"

    def test_way_over_limit_blocking(self):
        args = ", ".join(f"a{i}" for i in range(15))
        func = _parse_func(f"def f({args}): pass")
        issues = list(check_function_args("test.py", func, "f", _DEFAULTS))
        assert len(issues) == 1
        assert issues[0].severity == "blocking"


class TestFunctionLocals:
    def test_under_limit(self):
        func = _parse_func("def f():\n    x = 1\n    y = 2")
        issues = list(check_function_locals("test.py", func, "f", _DEFAULTS))
        assert len(issues) == 0

    def test_over_limit(self):
        body = "\n    ".join(f"v{i} = {i}" for i in range(20))
        func = _parse_func(f"def f():\n    {body}")
        issues = list(check_function_locals("test.py", func, "f", _DEFAULTS))
        assert len(issues) == 1
        assert issues[0].kind == "too-many-locals"


class TestFunctionStatements:
    def test_under_limit(self):
        func = _parse_func("def f():\n    x = 1\n    return x")
        issues = list(check_function_statements("test.py", func, "f", _DEFAULTS))
        assert len(issues) == 0

    def test_over_limit(self):
        stmts = "\n    ".join(f"x{i} = {i}" for i in range(60))
        func = _parse_func(f"def f():\n    {stmts}")
        issues = list(check_function_statements("test.py", func, "f", _DEFAULTS))
        assert len(issues) == 1
        assert issues[0].kind == "too-many-statements"


class TestFunctionReturns:
    def test_under_limit(self):
        func = _parse_func("""
            def f(x):
                if x > 0:
                    return 1
                return 0
        """)
        issues = list(check_function_returns("test.py", func, "f", _DEFAULTS))
        assert len(issues) == 0

    def test_over_limit(self):
        lines = ["def f(x):"]
        for i in range(8):
            lines.append(f"    if x == {i}: return {i}")
        source = "\n".join(lines) + "\n"
        func = _parse_func(source)
        issues = list(check_function_returns("test.py", func, "f", _DEFAULTS))
        assert len(issues) == 1
        assert issues[0].kind == "too-many-returns"


class TestNestingDepth:
    def test_under_limit(self):
        func = _parse_func("""
            def f(x):
                if x:
                    for i in range(10):
                        pass
        """)
        issues = list(check_nesting_depth("test.py", func, "f", _DEFAULTS))
        assert len(issues) == 0

    def test_over_limit(self):
        func = _parse_func("""
            def f(x):
                if x:
                    for i in range(10):
                        if i > 0:
                            while True:
                                if i % 2:
                                    pass
        """)
        issues = list(check_nesting_depth("test.py", func, "f", _DEFAULTS))
        assert len(issues) == 1
        assert issues[0].kind == "deep-nesting"


class TestCognitiveComplexity:
    def test_simple_function(self):
        func = _parse_func("""
            def f(x):
                return x + 1
        """)
        issues = list(check_cognitive_complexity("test.py", func, "f", _DEFAULTS))
        assert len(issues) == 0

    def test_complex_function(self):
        func = _parse_func("""
            def f(items):
                result = []
                for item in items:
                    if item.active:
                        if item.value > 0:
                            for sub in item.children:
                                if sub.valid:
                                    if sub.value > item.value:
                                        result.append(sub)
                                    elif sub.value == item.value:
                                        result.append(item)
                                    else:
                                        pass
                                else:
                                    continue
                        else:
                            pass
                    else:
                        pass
                return result
        """)
        issues = list(check_cognitive_complexity("test.py", func, "f", _DEFAULTS))
        assert len(issues) == 1
        assert issues[0].kind == "cognitive-complexity"


class TestCheckFunctionOrchestrator:
    def test_skips_dunder(self):
        func = _parse_func("def __init__(self): pass")
        issues = list(check_function("test.py", func, _DEFAULTS))
        assert len(issues) == 0

    def test_runs_all_checks(self):
        """A function that triggers multiple checks at once."""
        args = ", ".join(f"a{i}" for i in range(10))
        body = "\n    ".join(f"v{i} = {i}" for i in range(20))
        func = _parse_func(f"def big({args}):\n    {body}")
        issues = list(check_function("test.py", func, _DEFAULTS))
        kinds = {i.kind for i in issues}
        assert "too-many-args" in kinds
        assert "too-many-locals" in kinds


# ─── Class Checks ────────────────────────────────────────────────────


class TestClassAttributes:
    def test_under_limit(self):
        node = _parse_class("""
            class Foo:
                def __init__(self):
                    self.x = 1
                    self.y = 2
        """)
        issues = list(check_class_attributes("test.py", node, "Foo", _DEFAULTS))
        assert len(issues) == 0

    def test_over_limit(self):
        lines = ["class Foo:", "    def __init__(self):"]
        for i in range(15):
            lines.append(f"        self.a{i} = {i}")
        source = "\n".join(lines) + "\n"
        node = _parse_class(source)
        issues = list(check_class_attributes("test.py", node, "Foo", _DEFAULTS))
        assert len(issues) == 1
        assert issues[0].kind == "too-many-attributes"


class TestClassMethods:
    def test_under_limit(self):
        methods = "\n    ".join(f"def m{i}(self): pass" for i in range(5))
        node = _parse_class(f"class Foo:\n    {methods}")
        issues = list(check_class_methods("test.py", node, "Foo", _DEFAULTS))
        assert len(issues) == 0

    def test_over_limit(self):
        methods = "\n    ".join(f"def m{i}(self): pass" for i in range(25))
        node = _parse_class(f"class Foo:\n    {methods}")
        issues = list(check_class_methods("test.py", node, "Foo", _DEFAULTS))
        assert len(issues) == 1
        assert issues[0].kind == "too-many-methods"

    def test_dunder_excluded(self):
        """Dunder methods shouldn't count toward the limit."""
        dunders = "\n    ".join(f"def __m{i}__(self): pass" for i in range(25))
        node = _parse_class(f"class Foo:\n    {dunders}")
        issues = list(check_class_methods("test.py", node, "Foo", _DEFAULTS))
        assert len(issues) == 0


class TestClassParents:
    def test_under_limit(self):
        node = _parse_class("class Foo(A, B): pass")
        issues = list(check_class_parents("test.py", node, "Foo", _DEFAULTS))
        assert len(issues) == 0

    def test_over_limit(self):
        node = _parse_class("class Foo(A, B, C, D, E): pass")
        issues = list(check_class_parents("test.py", node, "Foo", _DEFAULTS))
        assert len(issues) == 1
        assert issues[0].kind == "too-many-parents"


class TestCheckClassOrchestrator:
    def test_runs_all_checks(self):
        """A class that triggers multiple checks."""
        lines = ["class GodClass(A, B, C, D, E):", "    def __init__(self):"]
        for i in range(15):
            lines.append(f"        self.a{i} = {i}")
        for i in range(25):
            lines.append(f"    def m{i}(self): pass")
        source = "\n".join(lines) + "\n"
        node = _parse_class(source)
        issues = list(check_class("test.py", node, _DEFAULTS))
        kinds = {i.kind for i in issues}
        assert "too-many-attributes" in kinds
        assert "too-many-methods" in kinds
        assert "too-many-parents" in kinds


# ─── StructureChecker Integration ────────────────────────────────────


class TestStructureCheckerIntegration:
    def test_full_pipeline(self, tmp_path):
        """Run StructureChecker on a file with known issues."""
        source = textwrap.dedent("""\
            class GodClass:
                def __init__(self):
        """)
        # Add 15 attributes
        for i in range(15):
            source += f"        self.a{i} = {i}\n"
        # Add many methods
        for i in range(25):
            source += f"    def m{i}(self): pass\n"

        filepath = tmp_path / "big.py"
        filepath.write_text(source)

        ctx = LinterContext(
            files=[str(filepath)],
            project_root=str(tmp_path),
            config={},
        )
        checker = StructureChecker()
        issues = list(checker.run(ctx))
        kinds = {i.kind for i in issues}
        assert "too-many-attributes" in kinds
        assert "too-many-methods" in kinds

    def test_clean_file_no_issues(self, tmp_path):
        """A clean file should produce no structure issues."""
        source = textwrap.dedent("""\
            def greet(name: str) -> str:
                return f"Hello, {name}"
        """)
        filepath = tmp_path / "clean.py"
        filepath.write_text(source)

        ctx = LinterContext(
            files=[str(filepath)],
            project_root=str(tmp_path),
            config={},
        )
        checker = StructureChecker()
        issues = list(checker.run(ctx))
        assert len(issues) == 0

    def test_custom_thresholds(self, tmp_path):
        """Custom threshold config should override defaults."""
        body = "\n    ".join(f"v{i} = {i}" for i in range(20))
        source = f"def big():\n    {body}\n"
        filepath = tmp_path / "big.py"
        filepath.write_text(source)

        ctx = LinterContext(
            files=[str(filepath)],
            project_root=str(tmp_path),
            config={"max_function_locals": 50},  # Raise limit to suppress issue
        )
        checker = StructureChecker()
        issues = list(checker.run(ctx))
        kinds = {i.kind for i in issues}
        assert "too-many-locals" not in kinds

"""Tests for #221: Nested handler decomposition — scope-aware exit detection and handler extraction.

Covers:
- Fix 1: Scope-aware _has_exit_statement
- Fix 2: Nested handler extraction
- Fix 3: Decorator-aware handler grouping
- Fix 4: Scaled _MAX_CANDIDATES
- Fix 5: Closure variable analysis
- Fix 6: Batch "decompose_register" prescription
"""

from __future__ import annotations

import ast
import textwrap

from lintgate.linters.structure_checks.dependency_clustering import (
    _analyze_closure,
    _has_exit_statement,
    _is_bag_of_handlers,
    _max_candidates,
    find_extraction_candidates,
)


def _parse_func(code: str) -> ast.FunctionDef:
    """Parse code and return the first function definition."""
    tree = ast.parse(textwrap.dedent(code))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise ValueError("No function found in code")


# ── Fix 1: Scope-aware exit detection ─────────────────────────────────


class TestScopeAwareExitDetection:
    def test_return_in_if_block_detected(self):
        """A return inside an if block IS detected (same scope, not nested func)."""
        code = """
        def f():
            if True:
                return 1
        """
        node = _parse_func(code)
        if_stmt = node.body[0]
        assert _has_exit_statement(if_stmt)

    def test_nested_funcdef_never_has_exit(self):
        """A FunctionDef node itself is never an exit — defining a function doesn't exit."""
        code = """
        def register():
            def handler():
                return 42
            handler()
        """
        node = _parse_func(code)
        nested_def = node.body[0]
        assert isinstance(nested_def, ast.FunctionDef)
        assert not _has_exit_statement(nested_def)

    def test_break_in_nested_class_not_detected(self):
        """A nested ClassDef is never an exit."""
        code = """
        def f():
            class Inner:
                def method(self):
                    for x in range(10):
                        break
        """
        node = _parse_func(code)
        class_def = node.body[0]
        assert not _has_exit_statement(class_def)

    def test_nested_handler_with_return_allows_extraction(self):
        """A function with nested handlers containing returns should produce candidates."""
        code = """
        def register(mcp):
            def tool_a():
                return {"result": "a"}
            def tool_b():
                return {"result": "b"}
            def tool_c():
                return {"result": "c"}
        """
        node = _parse_func(code)
        candidates = find_extraction_candidates(node, "test.py")
        # Should now produce candidates — the nested returns don't block extraction
        assert len(candidates) > 0


# ── Fix 2 & 3: Nested handler extraction with decorator awareness ─────


class TestNestedHandlerExtraction:
    def test_bag_of_handlers_detected(self):
        """Functions where >50% of body is nested functions are detected."""
        code = """
        def register(mcp):
            x = 1
            def handler_a():
                pass
            def handler_b():
                pass
            def handler_c():
                pass
        """
        node = _parse_func(code)
        assert _is_bag_of_handlers(node.body)

    def test_normal_function_not_bag_of_handlers(self):
        """Regular functions are NOT detected as bag-of-handlers."""
        code = """
        def process(data):
            x = 1
            y = 2
            z = x + y
            return z
        """
        node = _parse_func(code)
        assert not _is_bag_of_handlers(node.body)

    def test_handler_extraction_produces_candidates(self):
        """Nested handlers produce individual extraction prescriptions."""
        code = """
        def register(mcp):
            engine = mcp.engine
            def tool_a():
                return engine.run("a")
            def tool_b():
                return engine.run("b")
            def tool_c():
                return engine.run("c")
        """
        node = _parse_func(code)
        candidates = find_extraction_candidates(node, "tools.py")

        # Should have individual handler candidates + batch prescription
        handler_candidates = [c for c in candidates if c.kind == "extract_function"]
        assert len(handler_candidates) == 3

        # Each candidate should propose _impl_ prefix
        for c in handler_candidates:
            assert c.proposed_name.startswith("_impl_")

    def test_decorated_handlers_higher_confidence(self):
        """Handlers with recognized decorators get higher confidence."""
        code = """
        def register(app):
            @app.route("/a")
            def route_a():
                return "a"
            @app.route("/b")
            def route_b():
                return "b"
            @app.route("/c")
            def route_c():
                return "c"
        """
        node = _parse_func(code)
        candidates = find_extraction_candidates(node, "routes.py")
        handler_candidates = [c for c in candidates if c.kind == "extract_function"]

        # Decorated handlers should have higher confidence
        for c in handler_candidates:
            assert c.confidence >= 0.75  # base 0.65 + 0.15 decorator bonus - writes check
            assert "decorator_independence" in c.basis


# ── Fix 4: Scaled _MAX_CANDIDATES ─────────────────────────────────────


class TestScaledMaxCandidates:
    def test_low_cc_default_limit(self):
        assert _max_candidates(10) == 3

    def test_medium_cc_increased_limit(self):
        assert _max_candidates(35) == 6

    def test_high_cc_max_limit(self):
        assert _max_candidates(72) == 10


# ── Fix 5: Closure variable analysis ──────────────────────────────────


class TestClosureAnalysis:
    def test_reads_from_outer_scope(self):
        """Variables read from outer scope are captured."""
        code = """
        def register(mcp):
            engine = mcp.engine
            def tool():
                return engine.run()
        """
        node = _parse_func(code)
        nested = node.body[1]
        assert isinstance(nested, ast.FunctionDef)

        reads, writes = _analyze_closure(nested, {"engine", "mcp"})
        assert "engine" in reads
        assert len(writes) == 0

    def test_writes_to_outer_scope_detected(self):
        """Variables written to outer scope lower extraction confidence."""
        code = """
        def register(mcp):
            count = 0
            def tool():
                count = count + 1
                return count
        """
        node = _parse_func(code)
        nested = node.body[1]

        reads, writes = _analyze_closure(nested, {"count", "mcp"})
        assert "count" in writes

    def test_local_vars_not_captured(self):
        """Variables defined locally within the nested func are NOT captured."""
        code = """
        def register(mcp):
            engine = mcp.engine
            def tool():
                local_var = 42
                return local_var
        """
        node = _parse_func(code)
        nested = node.body[1]

        reads, writes = _analyze_closure(nested, {"engine", "mcp"})
        assert "local_var" not in reads

    def test_parameter_vars_not_captured(self):
        """Function parameters are NOT captured as outer-scope reads."""
        code = """
        def register(mcp):
            engine = mcp.engine
            def tool(engine):
                return engine.run()
        """
        node = _parse_func(code)
        nested = node.body[1]

        reads, writes = _analyze_closure(nested, {"engine", "mcp"})
        # engine is a parameter of tool(), so it should NOT be captured
        assert "engine" not in reads


# ── Fix 6: Batch decompose_register prescription ──────────────────────


class TestBatchDecomposeRegister:
    def test_batch_prescription_emitted(self):
        """When multiple handlers exist, a batch decompose_register is emitted."""
        code = """
        def register(mcp):
            def tool_a():
                return "a"
            def tool_b():
                return "b"
            def tool_c():
                return "c"
        """
        node = _parse_func(code)
        candidates = find_extraction_candidates(node, "tools.py")

        batch = [c for c in candidates if c.kind == "decompose_register"]
        assert len(batch) == 1
        assert batch[0].expected_delta.get("handlers") is not None
        assert len(batch[0].expected_delta["handlers"]) == 3

    def test_batch_prescription_includes_handler_metadata(self):
        """Batch prescription includes per-handler metadata."""
        code = """
        def register(mcp):
            def tool_a():
                x = 1
                return x
            def tool_b():
                y = 2
                return y
        """
        node = _parse_func(code)
        candidates = find_extraction_candidates(node, "tools.py")

        batch = [c for c in candidates if c.kind == "decompose_register"]
        assert len(batch) == 1

        handlers = batch[0].expected_delta["handlers"]
        names = {h["name"] for h in handlers}
        assert "tool_a" in names
        assert "tool_b" in names

    def test_single_handler_no_batch(self):
        """A single nested handler does NOT trigger batch prescription."""
        code = """
        def register(mcp):
            x = setup()
            y = setup2()
            z = setup3()
            def tool_a():
                return "a"
        """
        node = _parse_func(code)
        candidates = find_extraction_candidates(node, "tools.py")

        batch = [c for c in candidates if c.kind == "decompose_register"]
        assert len(batch) == 0

"""Tests for lintgate/channels/_symbol_extraction.py — all 3 functions."""

from __future__ import annotations

import os
import textwrap
import tempfile

from lintgate.channels._symbol_extraction import (
    _canonicalize_symbol_key,
    _visit_node,
    extract_symbol_spans,
)


# ─── _canonicalize_symbol_key ─────────────────────────────────────────────


def test_canonicalize_simple_relative():
    """Relative file within project root produces 'rel/path.py::symbol'."""
    result = _canonicalize_symbol_key("/proj/src/app.py", "my_func", "/proj")
    assert result == "src/app.py::my_func"


def test_canonicalize_with_class_method():
    """Symbol name with ClassName.method is preserved in the key."""
    result = _canonicalize_symbol_key("/proj/mod.py", "Foo.bar", "/proj")
    assert result == "mod.py::Foo.bar"


def test_canonicalize_same_dir():
    """File directly in project root produces just filename::symbol."""
    result = _canonicalize_symbol_key("/proj/utils.py", "helper", "/proj")
    assert result == "utils.py::helper"


def test_canonicalize_trailing_slash_root():
    """Trailing slash on project root is normalized correctly."""
    result = _canonicalize_symbol_key("/proj/src/a.py", "fn", "/proj/")
    assert result == "src/a.py::fn"


# ─── extract_symbol_spans ─────────────────────────────────────────────────


def _write_temp(content: str) -> tuple[str, str]:
    """Write content to a temp .py file and return (filepath, tmpdir)."""
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "mod.py")
    with open(path, "w") as f:
        f.write(textwrap.dedent(content))
    return path, tmpdir


def test_extract_simple_function():
    """Single top-level function produces one SymbolSpan."""
    path, root = _write_temp("""\
        def hello():
            return 1
    """)
    spans = extract_symbol_spans(path, root)
    assert len(spans) == 1
    assert spans[0].name == "hello"
    assert spans[0].is_method is False
    assert spans[0].class_name is None
    assert spans[0].start_line == 1
    assert spans[0].end_line == 2


def test_extract_method_in_class():
    """Method inside a class has is_method=True and correct class_name."""
    path, root = _write_temp("""\
        class MyClass:
            def do_thing(self):
                pass
    """)
    spans = extract_symbol_spans(path, root)
    assert len(spans) == 1
    assert spans[0].name == "MyClass.do_thing"
    assert spans[0].is_method is True
    assert spans[0].class_name == "MyClass"


def test_extract_nested_function_skipped():
    """Nested functions are skipped — only the outer function appears."""
    path, root = _write_temp("""\
        def outer():
            def inner():
                pass
            return inner()
    """)
    spans = extract_symbol_spans(path, root)
    assert len(spans) == 1
    assert spans[0].name == "outer"


def test_extract_decorated_function():
    """Decorator-aware start line: start_line is the decorator line."""
    path, root = _write_temp("""\
        import functools

        @functools.cache
        def cached_fn():
            return 42
    """)
    spans = extract_symbol_spans(path, root)
    assert len(spans) == 1
    assert spans[0].name == "cached_fn"
    # The decorator is on line 3, function def on line 4
    assert spans[0].start_line == 3
    assert spans[0].end_line == 5


def test_extract_returns_empty_for_syntax_error():
    """Files with syntax errors return an empty list (graceful degradation)."""
    path, root = _write_temp("def broken(:\n")
    spans = extract_symbol_spans(path, root)
    assert spans == []


def test_extract_returns_empty_for_nonexistent_file():
    """Missing file returns an empty list."""
    spans = extract_symbol_spans("/nonexistent/file.py", "/nonexistent")
    assert spans == []


def test_extract_multiple_functions_and_methods():
    """Multiple top-level functions and class methods all extracted."""
    path, root = _write_temp("""\
        def standalone():
            pass

        class Svc:
            def run(self):
                pass

            def stop(self):
                pass
    """)
    spans = extract_symbol_spans(path, root)
    names = [s.name for s in spans]
    assert names == ["standalone", "Svc.run", "Svc.stop"]


def test_extract_async_function():
    """Async function defs are extracted just like sync ones."""
    path, root = _write_temp("""\
        async def fetch():
            return None
    """)
    spans = extract_symbol_spans(path, root)
    assert len(spans) == 1
    assert spans[0].name == "fetch"


# ─── _visit_node (indirect through extract_symbol_spans) ──────────────────


def test_visit_node_symbol_key_format():
    """Symbol keys follow the canonical 'relpath.py::name' format."""
    path, root = _write_temp("""\
        def alpha():
            pass
    """)
    spans = extract_symbol_spans(path, root)
    assert "::" in spans[0].symbol_key
    assert spans[0].symbol_key.endswith("::alpha")


def test_visit_node_class_method_key():
    """Class method symbol key includes ClassName.method_name."""
    path, root = _write_temp("""\
        class Engine:
            def start(self):
                pass
    """)
    spans = extract_symbol_spans(path, root)
    assert spans[0].symbol_key.endswith("::Engine.start")


def test_visit_node_depth_blocks_nested():
    """Nested function inside a method is also skipped (depth>0)."""
    path, root = _write_temp("""\
        class Outer:
            def method(self):
                def helper():
                    pass
                return helper()
    """)
    spans = extract_symbol_spans(path, root)
    assert len(spans) == 1
    assert spans[0].name == "Outer.method"

"""Tests for lintgate/channels/_test_hygiene_finders.py — stub and weak assertion finders."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path  # noqa: TC003

from lintgate.channels._test_hygiene_finders import (
    _TOP_N_FINDINGS,
    _is_stub_body,
    _thygiene001_stub_tests,
    _thygiene002_weak_only,
)


def _make_func(body_src: str) -> ast.FunctionDef:
    """Parse a function definition and return its AST node."""
    src = textwrap.dedent(f"""\
        def test_example():
        {textwrap.indent(body_src, '    ')}
    """)
    tree = ast.parse(src)
    return tree.body[0]


# --- _is_stub_body ---


def test_is_stub_body_pass():
    node = _make_func("pass")
    assert _is_stub_body(node) == "pass"


def test_is_stub_body_ellipsis():
    node = _make_func("...")
    assert _is_stub_body(node) == "ellipsis"


def test_is_stub_body_not_implemented():
    node = _make_func("raise NotImplementedError")
    assert _is_stub_body(node) == "not_implemented"


def test_is_stub_body_not_implemented_call():
    node = _make_func('raise NotImplementedError("todo")')
    assert _is_stub_body(node) == "not_implemented"


def test_is_stub_body_empty_after_docstring():
    src = textwrap.dedent("""\
        def test_example():
            \"\"\"A docstring.\"\"\"
    """)
    tree = ast.parse(src)
    node = tree.body[0]
    assert _is_stub_body(node) == "empty"


def test_is_stub_body_real_body_returns_none():
    node = _make_func("assert 1 == 1")
    assert _is_stub_body(node) is None


def test_is_stub_body_multiple_stmts_returns_none():
    src = textwrap.dedent("""\
        def test_example():
            x = 1
            assert x == 1
    """)
    tree = ast.parse(src)
    node = tree.body[0]
    assert _is_stub_body(node) is None


# --- _thygiene001_stub_tests ---


def test_thygiene001_finds_stub(tmp_path: Path):
    test_file = tmp_path / "test_stub.py"
    test_file.write_text(textwrap.dedent("""\
        def test_placeholder():
            pass

        def test_real():
            assert 1 == 1
    """))
    findings = _thygiene001_stub_tests([str(test_file)])
    assert len(findings) == 1
    assert findings[0].kind == "THYGIENE001"
    assert findings[0].evidence["body_type"] == "pass"
    assert findings[0].evidence["function"] == "test_placeholder"
    assert findings[0].severity == "warning"
    assert findings[0].confidence == 0.95


def test_thygiene001_no_findings_for_real_tests(tmp_path: Path):
    test_file = tmp_path / "test_real.py"
    test_file.write_text(textwrap.dedent("""\
        def test_add():
            assert 1 + 1 == 2
    """))
    findings = _thygiene001_stub_tests([str(test_file)])
    assert len(findings) == 0


def test_thygiene001_class_method_stub(tmp_path: Path):
    test_file = tmp_path / "test_cls.py"
    test_file.write_text(textwrap.dedent("""\
        class TestMyClass:
            def test_todo(self):
                ...
    """))
    findings = _thygiene001_stub_tests([str(test_file)])
    assert len(findings) == 1
    assert findings[0].evidence["function"] == "TestMyClass.test_todo"
    assert findings[0].evidence["body_type"] == "ellipsis"


# --- _thygiene002_weak_only (integration with assertion classifier) ---


def test_thygiene002_returns_list(tmp_path: Path):
    test_file = tmp_path / "test_weak.py"
    test_file.write_text(textwrap.dedent("""\
        def test_exists():
            x = get_thing()
            assert x is not None
    """))
    # The function should not crash regardless of classifier availability
    findings = _thygiene002_weak_only([str(test_file)])
    assert isinstance(findings, list)


def test_top_n_findings_constant():
    assert _TOP_N_FINDINGS == 5

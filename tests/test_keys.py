"""Phase 2: Unit tests for canonical key functions."""

from __future__ import annotations

import pytest

from lintgate.keys import (
    canonical_function_key,
    canonical_relpath,
    parse_function_key,
    try_parse_function_key,
)


def test_canonical_function_key_format():
    """Canonical key is 'relpath.py::qualname'."""
    key = canonical_function_key("foo.py", "bar")
    assert key == "foo.py::bar"

    key = canonical_function_key("src/module.py", "Class.method")
    assert key == "src/module.py::Class.method"


def test_canonical_function_key_rejects_no_py():
    """ValueError for relpath without .py extension."""
    with pytest.raises(ValueError, match="must end with .py"):
        canonical_function_key("foo", "bar")

    with pytest.raises(ValueError, match="must end with .py"):
        canonical_function_key("foo.pyx", "bar")


def test_parse_roundtrip():
    """parse(canonical(r, q)) == (r, q)."""
    relpath = "src/module.py"
    qualname = "Class.method"
    key = canonical_function_key(relpath, qualname)
    parsed_relpath, parsed_qualname = parse_function_key(key)
    assert parsed_relpath == relpath
    assert parsed_qualname == qualname


def test_parse_function_key_rejects_no_separator():
    """ValueError for key without '::'."""
    with pytest.raises(ValueError, match="must contain '::'"):
        parse_function_key("bare_func_name")


def test_try_parse_handles_bare_name():
    """try_parse returns None for bare function names."""
    result = try_parse_function_key("bare_func_name")
    assert result is None


def test_try_parse_handles_qualified_key():
    """try_parse returns (relpath, qualname) for valid keys."""
    result = try_parse_function_key("foo.py::bar")
    assert result == ("foo.py", "bar")


def test_canonical_relpath_preserves_extension():
    """Key regression test for bug #2: relpath must preserve .py extension."""
    rel = canonical_relpath("/project/src/foo.py", "/project")
    assert rel.endswith(".py")
    assert rel == "src/foo.py"


def test_canonical_relpath_with_nested_path():
    """Nested paths are handled correctly."""
    rel = canonical_relpath("/project/src/deep/module.py", "/project")
    assert rel == "src/deep/module.py"

"""Tests for incremental test signal — _detect_new_functions (Deliverable B, Gap 7).

Verifies that Write and Edit tool uses correctly detect newly added
function definitions, with graceful handling of edge cases.
"""

from __future__ import annotations

import textwrap

from lintgate.hook_posttooluse import _detect_new_functions


class TestDetectNewFunctionWrite:
    """Write tool with function defs → detected."""

    def test_write_with_two_defs(self) -> None:
        """Write with 2 defs → both detected."""
        content = textwrap.dedent("""\
            import os

            def hello():
                print("hello")

            def world():
                print("world")
        """)
        result = _detect_new_functions(
            "Write",
            {"content": content, "file_path": "/tmp/test.py"},
            "/tmp",
        )
        assert result is not None
        assert len(result) == 2
        names = {r["name"] for r in result}
        assert names == {"hello", "world"}
        assert all(r["file"] == "/tmp/test.py" for r in result)

    def test_write_with_async_def(self) -> None:
        """Write with async def → detected."""
        content = textwrap.dedent("""\
            async def fetch_data():
                pass
        """)
        result = _detect_new_functions(
            "Write",
            {"content": content, "file_path": "/tmp/async_mod.py"},
            "/tmp",
        )
        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "fetch_data"

    def test_write_no_functions(self) -> None:
        """Write without functions → None."""
        content = "x = 42\ny = 'hello'\n"
        result = _detect_new_functions(
            "Write",
            {"content": content, "file_path": "/tmp/constants.py"},
            "/tmp",
        )
        assert result is None

    def test_write_non_python_file(self) -> None:
        """Write to non-.py file → None."""
        result = _detect_new_functions(
            "Write",
            {"content": "function hello() {}", "file_path": "/tmp/test.js"},
            "/tmp",
        )
        assert result is None

    def test_write_empty_content(self) -> None:
        """Write with empty content → None."""
        result = _detect_new_functions(
            "Write",
            {"content": "", "file_path": "/tmp/empty.py"},
            "/tmp",
        )
        assert result is None

    def test_write_class_methods_not_detected(self) -> None:
        """Write with class methods → only top-level defs detected."""
        content = textwrap.dedent("""\
            class MyClass:
                def method(self):
                    pass

            def top_level():
                pass
        """)
        result = _detect_new_functions(
            "Write",
            {"content": content, "file_path": "/tmp/cls.py"},
            "/tmp",
        )
        assert result is not None
        # Only top-level functions, not class methods
        assert len(result) == 1
        assert result[0]["name"] == "top_level"


class TestDetectNewFunctionEdit:
    """Edit tool adding def → detected."""

    def test_edit_adding_def(self) -> None:
        """Edit adding `def` → detected."""
        result = _detect_new_functions(
            "Edit",
            {
                "old_string": "x = 42\n",
                "new_string": "x = 42\n\ndef new_func():\n    pass\n",
                "file_path": "/tmp/module.py",
            },
            "/tmp",
        )
        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "new_func"

    def test_edit_adding_async_def(self) -> None:
        """Edit adding async def → detected."""
        result = _detect_new_functions(
            "Edit",
            {
                "old_string": "pass\n",
                "new_string": "pass\n\nasync def do_async():\n    await something()\n",
                "file_path": "/tmp/async_mod.py",
            },
            "/tmp",
        )
        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "do_async"


class TestNoNewFunctionEdit:
    """Body-only edit → not detected."""

    def test_body_only_edit(self) -> None:
        """Edit modifying body (no new def) → None."""
        result = _detect_new_functions(
            "Edit",
            {
                "old_string": "    return 42\n",
                "new_string": "    return 99\n",
                "file_path": "/tmp/module.py",
            },
            "/tmp",
        )
        assert result is None

    def test_def_already_in_old(self) -> None:
        """def exists in both old and new → not detected as new."""
        result = _detect_new_functions(
            "Edit",
            {
                "old_string": "def existing():\n    return 1\n",
                "new_string": "def existing():\n    return 2\n",
                "file_path": "/tmp/module.py",
            },
            "/tmp",
        )
        assert result is None

    def test_non_python_file_edit(self) -> None:
        """Edit to non-.py file → None."""
        result = _detect_new_functions(
            "Edit",
            {
                "old_string": "old",
                "new_string": "def new():\n    pass\n",
                "file_path": "/tmp/test.txt",
            },
            "/tmp",
        )
        assert result is None


class TestSyntaxErrorGraceful:
    """Malformed code → None, no exception."""

    def test_write_syntax_error(self) -> None:
        """Write with syntax error → None."""
        result = _detect_new_functions(
            "Write",
            {"content": "def broken(\n", "file_path": "/tmp/bad.py"},
            "/tmp",
        )
        assert result is None

    def test_write_missing_file_path(self) -> None:
        """Write with missing file_path → None."""
        result = _detect_new_functions(
            "Write",
            {"content": "def hello(): pass\n"},
            "/tmp",
        )
        assert result is None


class TestUnsupportedTool:
    """Non-Write/Edit tool → None."""

    def test_bash_tool(self) -> None:
        result = _detect_new_functions(
            "Bash",
            {"command": "echo hello"},
            "/tmp",
        )
        assert result is None

    def test_read_tool(self) -> None:
        result = _detect_new_functions(
            "Read",
            {"file_path": "/tmp/test.py"},
            "/tmp",
        )
        assert result is None

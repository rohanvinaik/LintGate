"""Tests to prevent count drift across documentation files.

These tests assert that linter counts, MCP tool counts, and other key
numbers stay in sync across README.md, AGENTS.md, and docs/design.md.
Count drift has bitten before — one wrong number propagates through
every session that reads it.
"""

from __future__ import annotations

import ast
import os
import re

# Project root — tests/ is one level below
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_file(relative_path: str) -> str:
    """Read a file relative to project root."""
    path = os.path.join(PROJECT_ROOT, relative_path)
    with open(path) as f:
        return f.read()


def _docstring_lines(source: str) -> set[int]:
    """Return the set of 1-indexed line numbers that belong to docstrings."""
    lines: set[int] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return lines
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            ds = node.body[0]
            for ln in range(ds.lineno, (ds.end_lineno or ds.lineno) + 1):
                lines.add(ln)
    return lines


def _count_mcp_tools() -> int:
    """Count @mcp.tool() decorators in actual code, excluding docstrings.

    Uses AST to identify docstring line ranges, then counts regex matches
    only on non-docstring lines. This avoids false positives from code
    examples in module docstrings (e.g., micro_refresh.py).
    """
    count = 0
    tool_files = [os.path.join(PROJECT_ROOT, "mcp_server.py")]
    mcp_tools_dir = os.path.join(PROJECT_ROOT, "mcp_tools")
    if os.path.isdir(mcp_tools_dir):
        for fname in sorted(os.listdir(mcp_tools_dir)):
            if fname.endswith(".py"):
                tool_files.append(os.path.join(mcp_tools_dir, fname))

    for fpath in tool_files:
        with open(fpath) as f:
            source = f.read()
        ds_lines = _docstring_lines(source)
        for i, line in enumerate(source.splitlines(), 1):
            if i not in ds_lines and re.search(r"@mcp\.tool\(\)", line):
                count += 1
    return count


def _count_reference_tool_rows() -> int:
    """Count tool table rows in docs/reference.md (lines matching | `tool_name` |)."""
    content = _read_file("docs/reference.md")
    return len(re.findall(r"^\| `\w+`", content, re.MULTILINE))


class TestLinterCount:
    """Verify linter count consistency across docs."""

    def test_linter_count_in_readme(self):
        """README.md should reference 18 linters."""
        content = _read_file("README.md")
        assert "18 linters" in content or "Eighteen linters" in content, (
            "README.md does not mention '18 linters'"
        )

    def test_linter_count_in_design(self):
        """docs/design.md should reference 18 linters."""
        content = _read_file("docs/design.md")
        assert "18 linters" in content or "Eighteen linters" in content, (
            "docs/design.md does not mention '18 linters' or 'Eighteen linters'"
        )

    def test_linter_count_in_project_structure(self):
        """Project structure comments should reference 18 linter implementations."""
        readme = _read_file("README.md")
        assert "18 linter" in readme, "README.md project structure doesn't mention '18 linter'"


class TestMCPToolCount:
    """Verify MCP tool count consistency."""

    def test_mcp_tool_count_matches_docs(self):
        """@mcp.tool() count in code should match docs."""
        actual_count = _count_mcp_tools()
        readme = _read_file("README.md")
        agents = _read_file("AGENTS.md")

        # README and AGENTS should reference the actual count
        count_str = str(actual_count)
        assert (
            f"{count_str} " in readme or f"({count_str})" in readme or f"{count_str} " in agents
        ), (
            f"MCP tool count is {actual_count} but docs don't match. "
            f"Run: grep -Rho '@mcp.tool()' mcp_server.py mcp_tools/*.py | wc -l"
        )

    def test_skill_tool_count_matches(self):
        """SKILL.md should reference the current MCP tool count."""
        actual_count = _count_mcp_tools()
        skill = _read_file("SKILL.md")
        assert str(actual_count) in skill, (
            f"SKILL.md does not reference MCP tool count {actual_count}"
        )

    def test_integrate_uses_dynamic_tool_count(self):
        """integrate.sh should derive tool count from source of truth."""
        integrate = _read_file("integrate.sh")
        assert (
            'grep -Rho "@mcp.tool()" "$LINTGATE_DIR/mcp_server.py" "$LINTGATE_DIR/mcp_tools"'
            in integrate
        )
        assert "$TOOL_COUNT tools by cognitive mode" in integrate

    def test_mcp_tool_count_is_111(self):
        """Sanity check: we currently have 111 MCP tools."""
        actual = _count_mcp_tools()
        assert actual == 111, f"Expected 111 MCP tools, got {actual}"

    def test_reference_md_lists_all_tools(self):
        """docs/reference.md tool tables should list all MCP tools."""
        actual_code_count = _count_mcp_tools()
        listed_count = _count_reference_tool_rows()
        assert listed_count == actual_code_count, (
            f"reference.md lists {listed_count} tools but code has {actual_code_count}. "
            f"Check for missing tool table entries."
        )

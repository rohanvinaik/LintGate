"""Tests to prevent count drift across documentation files.

These tests assert that linter counts, MCP tool counts, and other key
numbers stay in sync across README.md, AGENTS.md, and docs/design.md.
Count drift has bitten before — one wrong number propagates through
every session that reads it.
"""

from __future__ import annotations

import os
import re

# Project root — tests/ is one level below
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_file(relative_path: str) -> str:
    """Read a file relative to project root."""
    path = os.path.join(PROJECT_ROOT, relative_path)
    with open(path) as f:
        return f.read()


def _count_mcp_tools() -> int:
    """Count @mcp.tool() decorators across mcp_server.py and mcp_tools/ — the source of truth."""
    count = 0
    # Count in main server file
    content = _read_file("mcp_server.py")
    count += len(re.findall(r"@mcp\.tool\(\)", content))
    # Count in domain modules
    mcp_tools_dir = os.path.join(PROJECT_ROOT, "mcp_tools")
    if os.path.isdir(mcp_tools_dir):
        for fname in os.listdir(mcp_tools_dir):
            if fname.endswith(".py"):
                fpath = os.path.join(mcp_tools_dir, fname)
                with open(fpath) as f:
                    count += len(re.findall(r"@mcp\.tool\(\)", f.read()))
    return count


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
        assert "18 linter" in readme, (
            "README.md project structure doesn't mention '18 linter'"
        )


class TestMCPToolCount:
    """Verify MCP tool count consistency."""

    def test_mcp_tool_count_matches_docs(self):
        """@mcp.tool() count in mcp_server.py should match docs."""
        actual_count = _count_mcp_tools()
        readme = _read_file("README.md")
        agents = _read_file("AGENTS.md")

        # README and AGENTS should reference the actual count
        count_str = str(actual_count)
        assert f"{count_str} " in readme or f"({count_str})" in readme or f"{count_str} " in agents, (
            f"MCP tool count is {actual_count} but docs don't match. "
            f"Run: grep -Rho '@mcp.tool()' mcp_server.py mcp_tools | wc -l"
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
        assert 'grep -Rho "@mcp.tool()" "$LINTGATE_DIR/mcp_server.py" "$LINTGATE_DIR/mcp_tools"' in integrate
        assert "$TOOL_COUNT tools by cognitive mode" in integrate

    def test_mcp_tool_count_is_32(self):
        """Sanity check: we currently have 32 MCP tools."""
        actual = _count_mcp_tools()
        assert actual == 32, f"Expected 32 MCP tools, got {actual}"

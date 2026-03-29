"""LintGate MCP Server — Lite profile for local models.

Exposes 8 high-level tools. Internal tools (lint_get_details, mutation_prescribe,
spec_file_analyze, etc.) are called automatically by the high-level tools via
next_actions or internally. The local model never needs to know about them.

Schema: ~800 tokens vs ~15K for the full server.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "lintgate",
    instructions=(
        "LintGate: code quality for Python projects. 8 tools.\n"
        "Start: getting_started(path) or check_project(path)\n"
        "After edits: after_edit(path)\n"
        "Before commit: before_commit(path)\n"
        "Improve tests: improve_tests(path, file)\n"
        "Simplify code: simplify(path, file, start_line, end_line, helper_name)\n"
    ),
)

# Import the full machinery (but don't expose it all as tools)
from mcp_server import _tool_funcs


@mcp.tool()
def getting_started(path: str) -> str:
    """Set up and orient on a project. Call this first.

    Auto-creates config, checks environment, returns action plan.
    """
    return _tool_funcs["getting_started"](path)


@mcp.tool()
def check_project(path: str) -> str:
    """Run a full project health check. Returns top blockers and next actions.

    Runs lint, tests, deps, git, and structure analysis in parallel.
    """
    return _tool_funcs["controlplane_run"](path=path, scope="changed", strictness="relaxed")


@mcp.tool()
def fix_lint(path: str) -> str:
    """Auto-fix all safe lint issues in the project.

    Applies ruff safe fixes (formatting, import sorting, simple corrections).
    Returns count of fixes applied.
    """
    return _tool_funcs["lint_fix"](path=path, dry_run=False)


@mcp.tool()
def after_edit(path: str, files: list[str] | None = None) -> str:
    """Run after editing files. Fast lint check on changed files.

    Args:
        path: Project root.
        files: Files edited. Auto-detects from git if not specified.
    """
    return _tool_funcs["after_edit"](path=path, files=files)


@mcp.tool()
def before_commit(path: str) -> str:
    """Run before committing. Checks lint + secrets on staged files."""
    return _tool_funcs["before_commit"](path=path)


@mcp.tool()
def improve_tests(path: str, file: str) -> str:
    """Analyze test quality for a file and prescribe improvements.

    Runs mutation sampling, then returns surviving categories with
    specific test suggestions. Each surviving mutant tells you what
    your tests don't constrain.

    Args:
        path: Project root.
        file: Python file to analyze (e.g., "src/core.py").
    """
    # Run sampling first
    sampling_result = _tool_funcs["mutation_run_sampling"](path=path, file=file)
    result = json.loads(sampling_result)

    # If there are survivors, get prescriptions
    if "analysis_id" in result:
        prescribe_result = _tool_funcs["mutation_prescribe"](path=path, file=file)
        return prescribe_result

    return sampling_result


@mcp.tool()
def simplify(
    path: str,
    file: str,
    start_line: int,
    end_line: int,
    helper_name: str,
    dry_run: bool = True,
) -> str:
    """Extract a block of code into a helper function.

    Analyzes variable flow (inputs, outputs, closures) and generates
    the extraction. Use dry_run=True first to preview.

    Args:
        path: Project root.
        file: File containing the block.
        start_line: First line to extract.
        end_line: Last line to extract.
        helper_name: Name for the new helper function.
        dry_run: Preview only (default True). Set False to apply.
    """
    return _tool_funcs["refactor_extract_method"](
        path=path, file=file, start_line=start_line,
        end_line=end_line, helper_name=helper_name, dry_run=dry_run,
    )


@mcp.tool()
def lint_files(files: list[str], project_root: str | None = None) -> str:
    """Lint specific files. Call after editing to check for issues.

    Args:
        files: List of file paths to lint.
        project_root: Project root (auto-detected if not specified).
    """
    return _tool_funcs["lint_files"](files=files, project_root=project_root)


def run_server() -> None:
    mcp.run()


if __name__ == "__main__":
    run_server()

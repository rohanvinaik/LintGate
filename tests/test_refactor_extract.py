"""Tests for refactor_extract.py — extract method refactoring."""
from __future__ import annotations

import os
import textwrap

from lintgate.refactor_extract import (
    _build_helper_code,
    _collect_assigned_names,
    _collect_used_names,
    extract_method,
)


# ── _collect_assigned_names: comprehension variables ────────────────


class TestCollectAssignedNamesComprehensions:
    """Comprehension iteration variables must be recognized as assigned."""

    def test_listcomp_variable_is_assigned(self) -> None:
        import ast
        tree = ast.parse("[c for c in items]")
        assigned = _collect_assigned_names(tree)
        assert "c" in assigned

    def test_dictcomp_variables_are_assigned(self) -> None:
        import ast
        tree = ast.parse("{k: v for k, v in pairs}")
        assigned = _collect_assigned_names(tree)
        assert "k" in assigned
        assert "v" in assigned

    def test_setcomp_variable_is_assigned(self) -> None:
        import ast
        tree = ast.parse("{x for x in items}")
        assigned = _collect_assigned_names(tree)
        assert "x" in assigned

    def test_genexp_variable_is_assigned(self) -> None:
        import ast
        tree = ast.parse("sum(x for x in items)")
        assigned = _collect_assigned_names(tree)
        assert "x" in assigned

    def test_nested_comprehension_variables(self) -> None:
        import ast
        tree = ast.parse("[c for row in matrix for c in row]")
        assigned = _collect_assigned_names(tree)
        assert "row" in assigned
        assert "c" in assigned

    def test_comprehension_var_not_treated_as_input(self) -> None:
        """End-to-end: comprehension variable should not become a function input."""
        import ast
        # Code block: result = [c for c in items if c > 0]
        # 'items' is an input; 'c' is NOT (it's the comprehension variable)
        tree = ast.parse("result = [c for c in items if c > 0]")
        assigned = _collect_assigned_names(tree)
        used = _collect_used_names(tree)
        # c is both used (Load in body/filter) and assigned (comprehension target)
        assert "c" in assigned
        assert "c" in used
        # So inputs = used - assigned should NOT include c
        inputs = used - assigned
        assert "c" not in inputs
        assert "items" in inputs


# ── _build_helper_code: call indentation ────────────────────────────


class TestBuildHelperCodeIndentation:
    """The call replacement must preserve the original block's indentation."""

    def test_call_preserves_block_indent(self) -> None:
        block_lines = [
            "        x = a + b\n",
            "        y = x * 2\n",
        ]
        block_indent = "        "  # 8 spaces
        _, _, call = _build_helper_code(
            helper_name="_helper",
            inputs=["a", "b"],
            outputs=["x", "y"],
            needs_init=set(),
            block_lines=block_lines,
            block_indent=block_indent,
            enclosing_col_offset=4,
        )
        # Call must start with the block indent, not be stripped
        assert call.startswith(block_indent), (
            f"Call should start with {block_indent!r} but got: {call!r}"
        )

    def test_call_no_outputs_preserves_indent(self) -> None:
        block_lines = ["    print(x)\n"]
        block_indent = "    "
        _, _, call = _build_helper_code(
            helper_name="_log",
            inputs=["x"],
            outputs=[],
            needs_init=set(),
            block_lines=block_lines,
            block_indent=block_indent,
            enclosing_col_offset=0,
        )
        assert call.startswith(block_indent)

    def test_call_single_output_preserves_indent(self) -> None:
        block_lines = ["        result = compute(a)\n"]
        block_indent = "        "
        _, _, call = _build_helper_code(
            helper_name="_compute",
            inputs=["a"],
            outputs=["result"],
            needs_init=set(),
            block_lines=block_lines,
            block_indent=block_indent,
            enclosing_col_offset=4,
        )
        assert call.startswith(block_indent)
        assert "result = _compute(a)" in call


# ── extract_method: end-to-end indentation ──────────────────────────


class TestExtractMethodIndentation:
    """End-to-end: extracted call site must preserve indentation in the file."""

    def test_applied_extraction_preserves_indent(self, tmp_path: object) -> None:
        src_dir = tmp_path  # type: ignore[assignment]
        src_file = os.path.join(str(src_dir), "module.py")
        with open(src_file, "w") as f:
            f.write(textwrap.dedent("""\
                def outer():
                    a = 1
                    b = 2
                    x = a + b
                    y = x * 2
                    return y
            """))

        result = extract_method(
            project_root=str(src_dir),
            file="module.py",
            start_line=4,
            end_line=5,
            helper_name="_compute",
            dry_run=False,
        )
        assert not result.errors

        with open(src_file) as f:
            content = f.read()

        # The call site must be indented inside outer(), not at column 0
        lines = content.split("\n")
        call_line = [l for l in lines if "_compute(" in l and "def " not in l]
        assert call_line, f"No call site found in:\n{content}"
        indent = len(call_line[0]) - len(call_line[0].lstrip())
        assert indent >= 4, (
            f"Call site should be indented (got {indent} spaces): {call_line[0]!r}"
        )

    def test_applied_extraction_file_parses(self, tmp_path: object) -> None:
        """After extraction, the resulting file must be valid Python."""
        import ast as _ast
        src_dir = tmp_path  # type: ignore[assignment]
        src_file = os.path.join(str(src_dir), "module.py")
        with open(src_file, "w") as f:
            f.write(textwrap.dedent("""\
                def outer():
                    items = [1, 2, 3]
                    total = 0
                    for x in items:
                        total += x
                    return total
            """))

        result = extract_method(
            project_root=str(src_dir),
            file="module.py",
            start_line=4,
            end_line=5,
            helper_name="_sum_items",
            dry_run=False,
        )
        assert not result.errors

        with open(src_file) as f:
            content = f.read()

        # Must parse without SyntaxError
        _ast.parse(content)


class TestExtractMethodComprehensionLeak:
    """Comprehension variables must not leak as input parameters."""

    def test_listcomp_var_not_in_inputs(self, tmp_path: object) -> None:
        src_dir = tmp_path  # type: ignore[assignment]
        src_file = os.path.join(str(src_dir), "module.py")
        with open(src_file, "w") as f:
            f.write(textwrap.dedent("""\
                def process(items):
                    filtered = [x for x in items if x > 0]
                    total = sum(filtered)
                    return total
            """))

        result = extract_method(
            project_root=str(src_dir),
            file="module.py",
            start_line=2,
            end_line=2,
            helper_name="_filter_positive",
            dry_run=True,
        )
        assert not result.errors
        # 'x' is the comprehension variable — must NOT be an input
        assert "x" not in result.inputs
        # 'items' IS an input
        assert "items" in result.inputs

    def test_nested_comp_vars_not_in_inputs(self, tmp_path: object) -> None:
        src_dir = tmp_path  # type: ignore[assignment]
        src_file = os.path.join(str(src_dir), "module.py")
        with open(src_file, "w") as f:
            f.write(textwrap.dedent("""\
                def flatten(matrix):
                    flat = [c for row in matrix for c in row]
                    return flat
            """))

        result = extract_method(
            project_root=str(src_dir),
            file="module.py",
            start_line=2,
            end_line=2,
            helper_name="_flatten",
            dry_run=True,
        )
        assert not result.errors
        assert "c" not in result.inputs
        assert "row" not in result.inputs
        assert "matrix" in result.inputs

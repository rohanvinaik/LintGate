"""Safe module refactoring tool — libcst-based import rewriting (#refactor_move).

1 MCP tool:
- refactor_move: Move a module with automatic import rewriting
"""

from __future__ import annotations

from mcp_tools._disk_helpers import tool_response


def register(mcp, helpers):
    """Register refactor_move tools on the shared MCP instance."""

    @mcp.tool()
    def refactor_move(
        path: str,
        source: str,
        destination: str,
        dry_run: bool = True,
        generate_shim: bool = True,
    ) -> str:
        """Move a Python module with automatic import rewriting.

        Scans the project for all imports of the source module and rewrites
        them to point to the destination. Uses libcst for precise AST-based
        rewriting when available; falls back to showing what needs changing
        via ast scan when libcst is not installed.

        String references in import-bearing call sites (mock.patch,
        monkeypatch.setattr, importlib.import_module) are also rewritten.
        Arbitrary string literals are NOT touched.

        Args:
            path: Project root path.
            source: Dotted module path to move FROM (e.g., "lintgate.old_module").
            destination: Dotted module path to move TO (e.g., "lintgate.new_module").
            dry_run: If True (default), only scan and report — no files changed.
            generate_shim: If True (default), generate a backward-compatibility
                shim at the old location after applying the move.
        """
        from mcp_tools._refactor_move_impl import impl_refactor_move

        return impl_refactor_move(helpers, path, source, destination, dry_run, generate_shim)

    @mcp.tool()
    def refactor_extract_method(
        path: str,
        file: str,
        start_line: int,
        end_line: int,
        helper_name: str,
        dry_run: bool = True,
    ) -> str:
        """Extract a block of code into a named helper function (same file).

        WHEN TO USE: After mutation_decompose or extraction_plan identifies
        a complex function that should be split. This handles the common case
        where refactor_move can't help — splitting a single function's body
        into helper functions within the same module.

        Analyzes the block to detect inputs (variables read) and outputs
        (variables written and used later), then generates the helper
        function signature and call replacement.

        Example:
            refactor_extract_method(
                path="/project",
                file="src/model_atlas/ingest.py",
                start_line=45,
                end_line=78,
                helper_name="_process_stream_batch",
                dry_run=True,
            )

        Args:
            path: Project root path.
            file: Relative path to the source file.
            start_line: First line of the block to extract (1-indexed).
            end_line: Last line of the block to extract (1-indexed).
            helper_name: Name for the extracted helper function.
            dry_run: Preview only (default True). Set False to apply.
        """
        from lintgate.refactor_move import extract_method

        project_root = helpers["_validate_project_root"](path)
        result = extract_method(
            project_root, file, start_line, end_line, helper_name, dry_run=dry_run,
        )
        output = result.to_dict()

        from lintgate.next_action import NextAction, serialize_next_actions
        actions = []
        if dry_run and not result.errors:
            actions.append(NextAction(
                tool="refactor_extract_method",
                args={"path": path, "file": file, "start_line": start_line,
                      "end_line": end_line, "helper_name": helper_name, "dry_run": False},
                reason="Apply the extraction",
            ))
        elif not dry_run and not result.errors:
            actions.append(NextAction(
                tool="lint_files", args={"path": path, "files": [file]},
                reason="Verify extraction didn't break imports",
            ))
        output["next_actions"] = serialize_next_actions(actions)

        # Decision-relevant details inline
        if result.errors:
            summary_msg = f"Extract failed: {'; '.join(result.errors[:3])}"
            # Parse nearest-function suggestion from error message to build corrected call
            for err in result.errors:
                if "Nearest function:" in err and "lines" in err:
                    import re
                    m = re.search(r"lines (\d+)-(\d+)", err)
                    if m:
                        fn_start, fn_end = int(m.group(1)), int(m.group(2))
                        output["suggested_call"] = {
                            "tool": "refactor_extract_method",
                            "args": {
                                "path": path, "file": file,
                                "start_line": fn_start + 1,
                                "end_line": fn_end,
                                "helper_name": helper_name,
                            },
                            "reason": f"Adjusted range to fit inside the nearest function (lines {fn_start}-{fn_end})",
                        }
                        break
        elif dry_run:
            inputs_str = ", ".join(result.inputs) if result.inputs else "none"
            outputs_str = ", ".join(result.outputs) if result.outputs else "none"
            n_lines = result.extracted_code.count("\n") if result.extracted_code else 0
            summary_msg = (
                f"Extract: {result.helper_signature or f'def {helper_name}()'}\n"
                f"  Inputs: {inputs_str}\n"
                f"  Outputs: {outputs_str}\n"
                f"  Lines: {n_lines}\n"
                f"\nApply with dry_run=false"
            )
        else:
            summary_msg = (
                f"Applied: {helper_name} extracted\n"
                f"  Replaced lines {start_line}-{end_line} with: {result.call_replacement}\n"
                f"  File: {file}"
            )
        return tool_response(output, "refactor_extract_method", project_root, summary_msg)

    return {"refactor_move": refactor_move, "refactor_extract_method": refactor_extract_method}

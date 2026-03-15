"""Refactor move implementation — orchestration and dry-run logic.

Private implementation module for refactor_move_tools.py.
"""

from __future__ import annotations

from typing import Any

from lintgate.next_action import NextAction, serialize_next_actions


def impl_refactor_move(
    helpers: Any,
    path: str,
    source: str,
    destination: str,
    dry_run: bool,
    generate_shim: bool,
) -> str:
    """Implement the refactor_move MCP tool."""
    from lintgate.refactor_move import refactor_move

    project_root = helpers["_validate_project_root"](path)

    result = refactor_move(
        project_root,
        source,
        destination,
        dry_run=dry_run,
        generate_shim_file=generate_shim,
    )

    output = result.to_dict()
    output["next_actions"] = serialize_next_actions(
        _build_next_actions(path, source, destination, dry_run, result)
    )

    return str(helpers["_json_dumps"](output, output_mode="compact"))


def _build_next_actions(
    path: str,
    source: str,
    destination: str,
    dry_run: bool,
    result: Any,
) -> list[NextAction]:
    """Build next_actions based on refactor_move result."""
    actions: list[NextAction] = []

    if dry_run and not result.errors:
        actions.append(
            NextAction(
                tool="refactor_move",
                args={
                    "path": path,
                    "source": source,
                    "destination": destination,
                    "dry_run": False,
                },
                reason=f"Apply the move: {len(result.references_found)} references to rewrite.",
                priority=1,
                safe=False,
            )
        )
    elif not dry_run and not result.errors:
        actions.append(
            NextAction(
                tool="controlplane_run",
                args={"path": path},
                reason="Verify project health after module move.",
                priority=1,
            )
        )
        # Collect affected files for targeted lint
        affected = sorted({r.file for r in result.references_found})[:10]
        if affected:
            actions.append(
                NextAction(
                    tool="lint_files",
                    args={"path": path, "files": affected},
                    reason="Check for import errors in affected files.",
                    priority=2,
                )
            )

    if result.degraded:
        actions.append(
            NextAction(
                tool="refactor_move",
                args={
                    "path": path,
                    "source": source,
                    "destination": destination,
                    "dry_run": True,
                },
                reason="Install libcst for auto-apply: pip install 'lintgate[refactor]'",
                priority=3,
                condition="after installing libcst",
            )
        )

    return actions

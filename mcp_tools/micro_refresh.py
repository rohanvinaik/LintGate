"""Tool-response micro-refresh — piggyback session context on MCP responses.

Every major MCP tool response carries a compact ``session_context`` block
(~60 tokens) so the model's view of session state stays fresh. This is the
cheapest surface: zero extra latency (model already reads the response),
survives compaction (most recent tool response is always in context).

Usage in tool modules::

    from mcp_tools.micro_refresh import attach_session_context

    def register(server, helpers):
        @server.tool()
        async def my_tool(...) -> str:
            result = do_work(...)
            return helpers["_json_dumps"](
                attach_session_context(result, project_root), "compact"
            )

All operations are fail-open — if RuntimeState is unavailable, the result
is returned unchanged.
"""

from __future__ import annotations

from typing import Any


def attach_session_context(
    result: dict[str, Any],
    project_root: str | None,
) -> dict[str, Any]:
    """Piggyback compact session context on an MCP tool response.

    Args:
        result: The tool response dict to augment.
        project_root: Project root path. If None, skips attachment.

    Returns:
        The same result dict with ``session_context`` key added (or unchanged on error).
    """
    if project_root is None:
        return result

    try:
        from lintgate.runtime_state import load_runtime_state

        runtime = load_runtime_state(project_root)
        if runtime is None:
            return result

        result["session_context"] = {
            "gen": runtime.generation,
            "mode": runtime.mode,
            "focus": [f.rsplit("/", 1)[-1] for f in runtime.active_files[:3]],
            "blocking": runtime.blocking_issues,
            "coherence": runtime.coherence_state,
            "test": runtime.last_test_status,
            "tokens_pct": round(runtime.estimated_tokens_pct, 1),
        }

        # Add proactive behavioral status if findings are pending
        if runtime.pending_behavioral_findings:
            count = len(runtime.pending_behavioral_findings)
            first = runtime.pending_behavioral_findings[0]
            result["behavior_status"] = {
                "pending_count": count,
                "hint": first.get("message", "Review behavioral observations"),
            }
    except Exception:
        pass  # Fail-open

    return result

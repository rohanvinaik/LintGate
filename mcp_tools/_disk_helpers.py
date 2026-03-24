"""Disk-first tool response helpers.

These live in their own module so tool files can import them directly,
avoiding any dependency on the helpers dict injection timing.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any


def save_analysis(data: Any, tool_name: str, project_root: str | None, *, run_id: str = "") -> str:
    """Write analysis output to .lintgate/analysis/<tool>/<id>.json. Returns filepath."""
    if not project_root:
        project_root = os.getcwd()
    analysis_dir = os.path.join(project_root, ".lintgate", "analysis", tool_name)
    os.makedirs(analysis_dir, exist_ok=True)
    serialized = json.dumps(data, separators=(",", ":"), default=str)
    content_hash = hashlib.sha256(serialized.encode()).hexdigest()[:10]
    filename = f"{run_id}.json" if run_id else f"{content_hash}.json"
    filepath = os.path.join(analysis_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(serialized)
    return filepath


def tool_response(
    data: Any,
    tool_name: str,
    project_root: str,
    summary: str,
    *,
    run_id: str = "",
    next_actions: list | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Save analysis to disk, return slim tool response (~100 tokens)."""
    filepath = save_analysis(data, tool_name, project_root, run_id=run_id)
    analysis_id = run_id or os.path.basename(filepath).removesuffix(".json")
    response: dict[str, Any] = {"analysis_id": analysis_id, "summary": summary, "file": filepath}
    if extra:
        response.update(extra)
    if next_actions:
        response["next_actions"] = next_actions
    return json.dumps(response, separators=(",", ":"), default=str)

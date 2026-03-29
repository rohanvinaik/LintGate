"""Disk-first tool response helpers.

These live in their own module so tool files can import them directly,
avoiding any dependency on the helpers dict injection timing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re as _re
import time as _time
from typing import Any

# Key patterns for auto-classifying dict keys into sections
_FINDINGS_KEYS = _re.compile(
    r"issues|findings|results|candidates|functions|prescriptions|skeletons|"
    r"blocking_issues|warning_issues|top_vulnerable|untested_functions|"
    r"checks|repairs|states|assertion_upgrades|changes|actions|steps|"
    r"questions|targets|staged_artifacts|proposed_artifacts|call_sites"
)
_METRICS_KEYS = _re.compile(
    r"count|rate|score|duration|sigma|total|mean|avg|trend|elapsed|"
    r"tokens|fixable|fix_rate|healthy|convergence_rate|spec_level|kill_rate"
)
_STATUS_KEYS = _re.compile(
    r"status|state|mode|phase|ready|scope|tier|coherence|healthy|aligned|"
    r"dry_run|written|applied|frozen"
)
_META_KEYS = _re.compile(
    r"run_id|project|timestamp|generated_at|tool|version|model_key|"
    r"spec_id|workflow_id|target_key|analysis_id|probe_version"
)


def _classify_sections(data: dict[str, Any]) -> dict[str, list[str]]:
    """Auto-classify dict keys into queryable sections."""
    sections: dict[str, list[str]] = {
        "meta": [],
        "status": [],
        "metrics": [],
        "findings": [],
        "actions": [],
        "config": [],
        "data": [],
    }
    for key in data:
        if key == "next_actions":
            sections["actions"].append(key)
        elif _META_KEYS.search(key):
            sections["meta"].append(key)
        elif _FINDINGS_KEYS.search(key):
            sections["findings"].append(key)
        elif _METRICS_KEYS.search(key):
            sections["metrics"].append(key)
        elif _STATUS_KEYS.search(key):
            sections["status"].append(key)
        elif key in ("config", "settings", "thresholds_used", "requirements"):
            sections["config"].append(key)
        elif isinstance(data[key], (dict, list)) and key not in ("summary",) or key not in ("summary",):
            sections["data"].append(key)
    # Remove empty sections
    return {k: v for k, v in sections.items() if v}


def save_analysis(data: Any, tool_name: str, project_root: str | None, *, run_id: str = "") -> str:
    """Write analysis output to .lintgate/analysis/<tool>/<id>.json.

    Wraps data in a standardized envelope with:
    - meta: tool name, timestamp, project
    - sections: auto-classified index of queryable paths
    - data: the original analysis output

    Returns filepath.
    """
    if not project_root:
        project_root = os.getcwd()
    analysis_dir = os.path.join(project_root, ".lintgate", "analysis", tool_name)
    os.makedirs(analysis_dir, exist_ok=True)

    # Build standardized envelope
    if isinstance(data, dict):
        sections = _classify_sections(data)
        envelope: dict[str, Any] = {
            "_envelope": "v1",
            "_meta": {
                "tool": tool_name,
                "timestamp": _time.time(),
                "project": project_root,
            },
            "_sections": sections,
        }
        # Merge original data at top level (envelope keys are prefixed with _)
        envelope.update(data)
        serialized = json.dumps(envelope, separators=(",", ":"), default=str)
    else:
        serialized = json.dumps(data, separators=(",", ":"), default=str)

    content_hash = hashlib.sha256(serialized.encode()).hexdigest()[:10]
    filename = f"{run_id}.json" if run_id else f"{content_hash}.json"
    filepath = os.path.join(analysis_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(serialized)
    return filepath


_SUMMARY_MAX_CHARS = 1500  # ~375 tokens — hard cap on summary length


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
    # Guard: truncate oversized summaries
    if len(summary) > _SUMMARY_MAX_CHARS:
        summary = summary[:_SUMMARY_MAX_CHARS - 50] + f"\n... [{len(summary) - _SUMMARY_MAX_CHARS + 50} chars truncated]"
    filepath = save_analysis(data, tool_name, project_root, run_id=run_id)
    analysis_id = run_id or os.path.basename(filepath).removesuffix(".json")
    response: dict[str, Any] = {"analysis_id": analysis_id, "summary": summary, "file": filepath}
    # Include section manifest so the model knows what's queryable
    if isinstance(data, dict):
        sections = _classify_sections(data)
        if sections:
            response["queryable_sections"] = list(sections.keys())
    if extra:
        response.update(extra)
    if next_actions:
        response["next_actions"] = next_actions
    return json.dumps(response, separators=(",", ":"), default=str)


_SAFE_JSON_MAX = 2048  # Auto-save to disk if serialized output exceeds this


def _safe_json(data: Any, **kwargs: Any) -> str:
    """Drop-in replacement for json.dumps that auto-saves large responses to disk.

    Small dicts (errors, status) pass through as-is.
    Large dicts get saved to .lintgate/analysis/auto_saved/ and return a slim pointer.
    """
    result = json.dumps(data, separators=(",", ":"), default=str)
    if len(result) <= _SAFE_JSON_MAX:
        return result
    if not isinstance(data, dict):
        return result
    # Small error/status returns: pass through
    if set(data.keys()) <= {"error", "note", "status", "message"}:
        return result
    # Auto-save and return pointer
    project_root = data.get("project_root", data.get("path", os.getcwd()))
    return wrap_impl_response(result, "auto_saved", project_root)


def wrap_impl_response(
    impl_result: str,
    tool_name: str,
    project_root: str,
    summary_fn: Any = None,
    *,
    run_id: str = "",
) -> str:
    """Convert a legacy json.dumps impl return into a disk-first tool_response.

    Use this to wrap impl functions that return json.dumps(dict) without
    modifying the impl itself. Parses the JSON, saves to disk, returns slim.

    If summary_fn is provided, it receives the parsed dict and should return
    a summary string. Otherwise a default summary is generated from top-level keys.
    """
    try:
        data = json.loads(impl_result)
    except (json.JSONDecodeError, TypeError):
        return impl_result  # Not JSON — pass through unchanged

    if not isinstance(data, dict):
        return impl_result  # Only wrap dicts

    # Small error/status returns: pass through without disk persistence
    if set(data.keys()) <= {"error", "note", "status", "message"}:
        return impl_result

    if summary_fn:
        summary = summary_fn(data)
    else:
        # Auto-summary from top-level keys and counts
        parts = []
        for k, v in list(data.items())[:5]:
            if isinstance(v, list):
                parts.append(f"{k}: {len(v)} items")
            elif isinstance(v, dict):
                parts.append(f"{k}: {len(v)} keys")
            elif isinstance(v, (int, float)) or isinstance(v, str) and len(v) < 80:
                parts.append(f"{k}={v}")
        summary = f"{tool_name}: {', '.join(parts)}" if parts else f"{tool_name}: done"

    next_actions = data.get("next_actions")
    return tool_response(data, tool_name, project_root, summary, run_id=run_id, next_actions=next_actions)

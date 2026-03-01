"""Compass tools — 4-axis project understanding, cognitive modes, and hook setup.

8 MCP tools:
- compass_status: Show axes, depths, gap report, staleness, mode
- compass_check: Check action against toward/away/forbidden directives
- compass_update: Re-extract + optionally render for targets
- compass_interview: Gap-filling interview (code inference first)
- compass_reset: Scoped state reset with dry-run default
- theory_mode_enter: Enter theory exploration mode
- theory_mode_freeze: Freeze compass, validate, exit to normal
- setup_hooks: Generate .claude/settings.json hook config
"""

from __future__ import annotations

import json
import os
from typing import Any

# ── Mode state helpers ───────────────────────────────────────────────


def _load_mode_dict(project_root: str) -> dict[str, Any]:
    """Load mode state dict from session memory."""
    try:
        from lintgate.controlplane.session_memory import get_or_create_session

        session = get_or_create_session(project_root)
        return session.behavior_compass.get("mode_state", {"current": "normal"})
    except Exception:
        return {"current": "normal"}


def _load_mode_obj(project_root: str) -> Any:
    """Load ModeState object from session memory."""
    from lintgate.modes.mode_state import ModeState

    return ModeState.from_dict(_load_mode_dict(project_root))


def _save_mode(project_root: str, mode_state: Any) -> None:
    """Persist ModeState to session memory."""
    try:
        from lintgate.controlplane.session_memory import (
            get_or_create_session,
            save_session,
        )

        session = get_or_create_session(project_root)
        session.behavior_compass["mode_state"] = mode_state.to_dict()
        save_session(session)
    except Exception:
        pass


# ── Hook config helpers ──────────────────────────────────────────────


def _build_hooks_config() -> dict[str, list[dict[str, Any]]]:
    """Build the hooks configuration dict."""
    base = "python -m lintgate.hooks"

    def _entry(
        module: str,
        *,
        timeout_s: int,
        matcher: str | None = None,
        async_hook: bool = False,
    ) -> dict[str, Any]:
        hook: dict[str, Any] = {
            "type": "command",
            "command": f"{base}.{module}",
            "timeout": timeout_s,
        }
        if async_hook:
            hook["async"] = True
        entry: dict[str, Any] = {"hooks": [hook]}
        if matcher:
            entry["matcher"] = matcher
        return entry

    return {
        "SessionStart": [_entry("session_start", timeout_s=5, matcher="startup")],
        "UserPromptSubmit": [_entry("user_prompt", timeout_s=2)],
        "PreToolUse": [
            _entry("pre_tool", timeout_s=3, matcher="Write|Edit|MultiEdit|Bash")
        ],
        "PreCompact": [_entry("pre_compact", timeout_s=5, matcher="auto|manual")],
        "Stop": [_entry("stop_gate", timeout_s=3)],
        "SessionEnd": [_entry("session_end", timeout_s=10, async_hook=True)],
    }


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep-merge override into base (non-destructive)."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        elif (
            key in result and isinstance(result[key], list) and isinstance(value, list)
        ):
            # Preserve existing user hooks and append new unique entries.
            merged = list(result[key])
            for item in value:
                if item not in merged:
                    merged.append(item)
            result[key] = merged
        else:
            result[key] = value
    return result


def _refresh_axis_scores(state: Any) -> None:
    """Recompute axis depth/summary after claim mutations."""
    from lintgate.compass import compute_axis_depth

    for axis in state.axes.values():
        axis.depth = compute_axis_depth(axis.claims)
        if axis.claims:
            best = max(axis.claims, key=lambda c: (c.confidence, len(c.text)))
            axis.summary = best.text
        else:
            axis.summary = ""


# ── Tool implementations ─────────────────────────────────────────────


def _impl_status(project_root: str, path: str) -> dict[str, Any]:
    """Implementation for compass_status."""
    from lintgate.compass import AXIS_NAMES, compute_staleness
    from lintgate.compass_io import load_compass

    compass = load_compass(project_root)
    if compass is None:
        return {
            "status": "no_compass",
            "message": "No compass found. Run compass_update to extract.",
            "next_actions": [
                {"tool": "compass_update", "args": {"path": path, "write": True}}
            ],
        }

    axes_info = {}
    for name in AXIS_NAMES:
        axis = compass.axes.get(name)
        axes_info[name] = {
            "depth": axis.depth if axis else 0,
            "claim_count": len(axis.claims) if axis else 0,
            "summary": (axis.summary[:120] if axis and axis.summary else ""),
        }

    staleness = compute_staleness(compass)
    next_actions: list[dict] = []
    if staleness > 0.8:
        next_actions.append({"tool": "compass_update", "reason": "Compass is stale"})
    if compass.gap_report.interview_recommended:
        next_actions.append({"tool": "compass_interview", "reason": "Gaps detected"})

    return {
        "axes": axes_info,
        "directives_count": len(compass.directives),
        "gap_report": compass.gap_report.to_dict(),
        "staleness": round(staleness, 2),
        "frozen": compass.frozen,
        "mode": _load_mode_dict(project_root).get("current", "normal"),
        "next_actions": next_actions,
    }


def _impl_check(project_root: str, action: str) -> dict[str, Any]:
    """Implementation for compass_check."""
    from lintgate.compass_io import load_compass
    from lintgate.modes.execution_compass import ExecutionCompass

    compass = load_compass(project_root)
    if compass is None:
        return {
            "aligned": None,
            "message": "Cannot evaluate — no compass loaded. Run compass_update first.",
        }

    ec = ExecutionCompass.from_compass_state(compass)
    result = ec.check_alignment(action)
    return {
        "aligned": result.get("aligned", True),
        "violations": result.get("violations", []),
        "warnings": result.get("warnings", []),
        "true_north": ec.true_north[:120] if ec.true_north else "",
    }


def _impl_update(
    project_root: str, targets: list[str] | None, write: bool
) -> dict[str, Any]:
    """Implementation for compass_update."""
    from lintgate.axis_extractor import extract_compass
    from lintgate.code_inference import infer_from_code
    from lintgate.compass import (
        AXIS_NAMES,
        FACET_TO_AXIS,
        CompassAxis,
        compute_compass_hash,
    )
    from lintgate.compass_io import save_compass
    from lintgate.gap_detector import detect_gaps

    state = extract_compass(project_root)
    inferred = infer_from_code(project_root)
    for claim in inferred:
        axis_name = FACET_TO_AXIS.get(claim.origin_facet, "world")
        if axis_name not in state.axes:
            state.axes[axis_name] = CompassAxis(name=axis_name)
        state.axes[axis_name].claims.append(claim)

    _refresh_axis_scores(state)
    detect_gaps(state)
    result: dict[str, Any] = {
        "compass_hash": compute_compass_hash(state),
        "axes": {
            name: {
                "depth": state.axes[name].depth,
                "claim_count": len(state.axes[name].claims),
            }
            for name in AXIS_NAMES
            if name in state.axes
        },
        "gap_report": state.gap_report.to_dict(),
        "inferred_claims": len(inferred),
    }
    if write:
        save_compass(project_root, state)
        result["written"] = True

    rendered = _render_targets(project_root, state, targets, write)
    if rendered:
        result["rendered"] = rendered
    return result


def _render_targets(
    project_root: str,
    state: Any,
    targets: list[str] | None,
    write: bool,
) -> dict[str, Any] | None:
    """Render context files for specified targets."""
    if not targets:
        return None
    try:
        import time

        from lintgate.renderers import build_default_registry

        registry = build_default_registry()
        if targets == ["all"]:
            targets = registry.detect_tools(project_root) or ["claude", "generic"]

        metadata = {"project_root": project_root, "generated_at": str(int(time.time()))}
        files = registry.render_for_targets(targets, state, metadata)
        if write:
            for rel_path, content in files.items():
                full = os.path.join(project_root, rel_path)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w") as f:
                    f.write(content)
        return {"targets": targets, "files": list(files.keys()), "written": write}
    except Exception as exc:
        return {"error": str(exc)}


def _impl_interview(
    project_root: str,
    path: str,
    answers: dict[str, str] | None,
    skip: bool,
) -> dict[str, Any]:
    """Implementation for compass_interview."""
    from lintgate.compass_io import load_compass, save_compass
    from lintgate.gap_detector import build_interview, detect_gaps, skip_interview

    compass = load_compass(project_root)
    if compass is None:
        return {
            "error": "No compass found. Run compass_update first.",
            "next_actions": [
                {"tool": "compass_update", "args": {"path": path, "write": True}}
            ],
        }
    if skip:
        skip_interview(compass)
        save_compass(project_root, compass)
        return {"status": "skipped"}
    if answers:
        applied = _apply_answers(project_root, compass, answers)
        return {"applied": applied, "gap_report": compass.gap_report.to_dict()}

    return {
        "gap_report": detect_gaps(compass).to_dict(),
        "questions": build_interview(compass.gap_report),
        "usage": 'Pass answers={"axis:idx": "your answer"} to apply.',
    }


def _apply_answers(
    project_root: str,
    compass: Any,
    answers: dict[str, str],
) -> list[dict[str, Any]]:
    """Apply interview answers to compass and persist."""
    from lintgate.compass_io import save_compass
    from lintgate.gap_detector import apply_answer

    applied: list[dict[str, Any]] = []
    for key, text in answers.items():
        parts = key.split(":", 1)
        if len(parts) != 2:
            continue
        try:
            idx = int(parts[1])
        except ValueError:
            continue
        claim = apply_answer(compass, parts[0], idx, text)
        applied.append({"axis": parts[0], "question_idx": idx, "claim": claim.text})
    save_compass(project_root, compass)
    return applied


def _impl_reset(
    project_root: str, path: str, scope: str, confirm: bool
) -> dict[str, Any]:
    """Implementation for compass_reset."""
    from lintgate.reset import (
        reset_compass_only,
        reset_global,
        reset_project,
        reset_session_only,
    )

    dry_run = not confirm
    fns = {
        "compass": lambda: reset_compass_only(project_root, dry_run=dry_run),
        "session": lambda: reset_session_only(project_root, dry_run=dry_run),
        "project": lambda: reset_project(project_root, dry_run=dry_run),
        "global": lambda: reset_global(dry_run=dry_run),
    }
    fn = fns.get(scope)
    if fn is None:
        return {"error": f"Invalid scope: {scope}"}
    report = fn()
    result: dict[str, Any] = {"scope": scope, "dry_run": dry_run, **report.to_dict()}
    if dry_run and report.deleted:
        result["next_actions"] = [
            {
                "tool": "compass_reset",
                "args": {"path": path, "scope": scope, "confirm": True},
            },
        ]
    return result


def _impl_theory_enter(project_root: str) -> dict[str, Any]:
    """Implementation for theory_mode_enter."""
    ms = _load_mode_obj(project_root)
    label = ms.enter_theory()
    if label is None:
        return {"error": f"Cannot enter theory from {ms.current.value}"}
    _save_mode(project_root, ms)
    return {"status": "entered", "mode": "theory", "transition": label}


def _impl_theory_freeze(project_root: str) -> dict[str, Any]:
    """Implementation for theory_mode_freeze."""
    from lintgate.compass import REQUIRED_AXES, compute_compass_hash
    from lintgate.compass_io import load_compass, save_compass

    ms = _load_mode_obj(project_root)
    compass = load_compass(project_root)
    if compass is None:
        return {"error": "No compass to freeze."}

    warnings = [
        f"Required axis '{a}' is empty"
        for a in REQUIRED_AXES
        if not compass.axes.get(a) or compass.axes[a].depth == 0
    ]
    ch = compute_compass_hash(compass)
    label = ms.freeze_theory(ch)
    if label is None:
        return {"error": f"Not in theory mode ({ms.current.value})"}

    compass.frozen, compass.frozen_hash = True, ch
    save_compass(project_root, compass)
    _save_mode(project_root, ms)
    return {"status": "frozen", "compass_hash": ch, "warnings": warnings}


def _impl_setup_hooks(project_root: str, write: bool) -> dict[str, Any]:
    """Implementation for setup_hooks."""
    hooks_config = _build_hooks_config()
    settings_path = os.path.join(project_root, ".claude", "settings.json")
    existing: dict = {}
    try:
        with open(settings_path) as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    merged = _deep_merge(existing, {"hooks": hooks_config})
    if write:
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        with open(settings_path, "w") as f:
            json.dump(merged, f, indent=2)
    return {
        "status": "written" if write else "preview",
        "path": settings_path,
        "hooks": hooks_config,
        "merged_settings": merged if not write else None,
    }


# ── Registration ─────────────────────────────────────────────────────


def register(mcp, helpers):
    """Register compass tools on the shared MCP instance."""
    jd = helpers["_json_dumps"]
    vr = helpers["_validate_project_root"]

    @mcp.tool()
    def compass_status(path: str) -> str:
        """Show compass axes, depths, gap report, staleness, and cognitive mode.

        Example: compass_status(path="/my/project")
        """
        return jd(_impl_status(vr(path), path))

    @mcp.tool()
    def compass_check(path: str, action: str) -> str:
        """Check an action against toward/away/forbidden directives.

        Args:
            path: Project root path.
            action: Description of the action to check.
        """
        return jd(_impl_check(vr(path), action))

    @mcp.tool()
    def compass_update(
        path: str,
        targets: list[str] | None = None,
        write: bool = False,
    ) -> str:
        """Re-extract compass from project docs and optionally render context files.

        Args:
            path: Project root path.
            targets: Render targets (e.g. ["claude", "cursor"], or ["all"]).
            write: Write compass.yaml and rendered files to disk (default False).

        Example: compass_update(path="/my/project", write=True)
        """
        project_root = vr(path)
        result = _impl_update(project_root, targets, write)
        next_actions: list[dict] = []
        if result.get("gap_report", {}).get("interview_recommended"):
            next_actions.append({"tool": "compass_interview", "args": {"path": path}})
        if not write:
            next_actions.append(
                {"tool": "compass_update", "args": {"path": path, "write": True}}
            )
        result["next_actions"] = next_actions
        return jd(result)

    @mcp.tool()
    def compass_interview(
        path: str,
        answers: dict[str, str] | None = None,
        skip: bool = False,
    ) -> str:
        """Gap-filling interview — returns questions or applies answers.

        Args:
            path: Project root path.
            answers: Dict mapping "axis:question_idx" to answer text.
            skip: Set True to dismiss the interview recommendation.
        """
        return jd(_impl_interview(vr(path), path, answers, skip))

    @mcp.tool()
    def compass_reset(path: str, scope: str = "compass", confirm: bool = False) -> str:
        """Scoped state reset with dry-run default.

        Args:
            path: Project root path.
            scope: "compass" | "session" | "project" | "global".
            confirm: Set True to actually delete (default False = dry run).
        """
        return jd(_impl_reset(vr(path), path, scope, confirm))

    @mcp.tool()
    def theory_mode_enter(path: str) -> str:
        """Enter theory exploration mode. Normal->Theory allowed; Habit->Theory blocked."""
        return jd(_impl_theory_enter(vr(path)))

    @mcp.tool()
    def theory_mode_freeze(path: str) -> str:
        """Freeze compass and exit theory mode to normal."""
        return jd(_impl_theory_freeze(vr(path)))

    @mcp.tool()
    def setup_hooks(path: str, write: bool = False) -> str:
        """Generate .claude/settings.json hook configuration for compass hooks.

        Args:
            path: Project root path.
            write: Write settings to disk (default False = preview).
        """
        return jd(_impl_setup_hooks(vr(path), write))

    return {
        "compass_status": compass_status,
        "compass_check": compass_check,
        "compass_update": compass_update,
        "compass_interview": compass_interview,
        "compass_reset": compass_reset,
        "theory_mode_enter": theory_mode_enter,
        "theory_mode_freeze": theory_mode_freeze,
        "setup_hooks": setup_hooks,
    }

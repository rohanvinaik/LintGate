#!/usr/bin/env python3
"""Compass management — standalone.

Commands:
    status PATH
    check PATH --action "..."
    update PATH [--target TARGET ...] [--write]
    interview PATH [--answer 'axis:idx=text' ...] [--skip]
    reset PATH --scope compass|session|project|global [--confirm]
    theory-enter PATH
    theory-freeze PATH
    setup-hooks PATH [--write]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from lintgate.compass_helpers import (
    _impl_check,
    _impl_interview,
    _impl_reset,
    _impl_setup_hooks,
    _impl_status,
    _impl_theory_enter,
    _impl_theory_freeze,
    _impl_update,
)
from scripts._common import emit, validate_project_root


def _parse_answers(entries: list[str]) -> dict[str, str]:
    """Parse --answer 'axis:idx=text' entries into {axis:idx -> text}."""
    out: dict[str, str] = {}
    for e in entries:
        if "=" not in e:
            continue
        key, _, val = e.partition("=")
        out[key.strip()] = val
    return out


# ── Command handlers ──────────────────────────────────────────────────────


def cmd_status(args: argparse.Namespace) -> None:
    project_root = validate_project_root(args.path)
    result = _impl_status(project_root, args.path)
    if result.get("status") == "no_compass":
        print(json.dumps(result, separators=(",", ":"), default=str))
        return
    axes = result.get("axes", {})
    depths = {k: v.get("depth", 0) for k, v in axes.items()}
    staleness = result.get("staleness", 0)
    mode = result.get("mode", "normal")
    summary = (
        f"Compass: {sum(1 for d in depths.values() if d > 0)}/{len(depths)} axes populated. "
        f"Staleness: {staleness}. Mode: {mode}."
    )
    emit(
        result, "compass_status", project_root, summary,
        next_actions=result.get("next_actions"),
    )


def cmd_check(args: argparse.Namespace) -> None:
    project_root = validate_project_root(args.path)
    result = _impl_check(project_root, args.action)
    if result.get("aligned") is None:
        print(json.dumps(result, separators=(",", ":"), default=str))
        return
    aligned = result.get("aligned", True)
    n_violations = len(result.get("violations", []))
    n_warnings = len(result.get("warnings", []))
    summary = (
        f"Alignment: {'OK' if aligned else 'BLOCKED'}. "
        f"Violations: {n_violations}. Warnings: {n_warnings}."
    )
    emit(result, "compass_check", project_root, summary)


def cmd_update(args: argparse.Namespace) -> None:
    project_root = validate_project_root(args.path)
    targets = args.target or None
    result = _impl_update(project_root, targets, args.write)
    next_actions: list[dict] = []
    if result.get("gap_report", {}).get("interview_recommended"):
        next_actions.append({"tool": "compass_interview", "args": {"path": args.path}})
    if not args.write:
        next_actions.append({"tool": "compass_update", "args": {"path": args.path, "write": True}})
    result["next_actions"] = next_actions
    axes = result.get("axes", {})
    inferred = result.get("inferred_claims", 0)
    written = result.get("written", False)
    summary = f"Compass updated: {len(axes)} axes, {inferred} inferred claims. Written: {written}."
    emit(result, "compass_update", project_root, summary, next_actions=next_actions)


def cmd_interview(args: argparse.Namespace) -> None:
    project_root = validate_project_root(args.path)
    answers = _parse_answers(args.answer) if args.answer else None
    result = _impl_interview(project_root, args.path, answers, args.skip)
    if "error" in result or result.get("status") == "skipped":
        print(json.dumps(result, separators=(",", ":"), default=str))
        return
    if "applied" in result:
        summary = f"Interview: {len(result['applied'])} answers applied."
        emit(result, "compass_interview", project_root, summary)
        return
    questions = result.get("questions", [])
    summary = f"Interview: {len(questions)} questions. Pass answers to apply."
    emit(
        result, "compass_interview", project_root, summary,
        next_actions=[{"tool": "compass_interview", "args": {"path": args.path, "answers": "..."}}],
    )


def cmd_reset(args: argparse.Namespace) -> None:
    project_root = validate_project_root(args.path)
    result = _impl_reset(project_root, args.path, args.scope, args.confirm)
    if "error" in result:
        print(json.dumps(result, separators=(",", ":"), default=str))
        return
    dry_run = result.get("dry_run", True)
    deleted = result.get("deleted", [])
    summary = (
        f"Reset {args.scope}: {'dry-run' if dry_run else 'applied'}. "
        f"{len(deleted)} items {'would be ' if dry_run else ''}deleted."
    )
    emit(
        result, "compass_reset", project_root, summary,
        next_actions=result.get("next_actions"),
    )


def cmd_theory_enter(args: argparse.Namespace) -> None:
    project_root = validate_project_root(args.path)
    result = _impl_theory_enter(project_root)
    print(json.dumps(result, separators=(",", ":"), default=str))


def cmd_theory_freeze(args: argparse.Namespace) -> None:
    project_root = validate_project_root(args.path)
    result = _impl_theory_freeze(project_root)
    if "error" in result:
        print(json.dumps(result, separators=(",", ":"), default=str))
        return
    warnings_count = len(result.get("warnings", []))
    prescriptive = result.get("prescriptive_specs", {})
    auto_composed = len(prescriptive.get("auto_composed", [])) if prescriptive else 0
    summary = (
        f"Compass frozen. Hash: {result.get('compass_hash', '?')[:10]}. "
        f"Warnings: {warnings_count}. Auto-composed specs: {auto_composed}."
    )
    emit(result, "theory_mode_freeze", project_root, summary)


def cmd_setup_hooks(args: argparse.Namespace) -> None:
    project_root = validate_project_root(args.path)
    result = _impl_setup_hooks(project_root, args.write)
    status = result.get("status", "preview")
    hooks_count = len(result.get("hooks", {}))
    summary = (
        f"Hooks config: {status}. {hooks_count} hook events configured. "
        f"Path: {result.get('path', '')}."
    )
    emit(result, "setup_hooks", project_root, summary)


def main() -> None:
    parser = argparse.ArgumentParser(prog="compass_manage", description="Compass management")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status")
    p_status.add_argument("path")

    p_check = sub.add_parser("check")
    p_check.add_argument("path")
    p_check.add_argument("--action", required=True)

    p_update = sub.add_parser("update")
    p_update.add_argument("path")
    p_update.add_argument("--target", action="append", default=[])
    p_update.add_argument("--write", action="store_true")

    p_int = sub.add_parser("interview")
    p_int.add_argument("path")
    p_int.add_argument(
        "--answer",
        action="append",
        default=[],
        help="axis:idx=text; repeat for multiple answers",
    )
    p_int.add_argument("--skip", action="store_true")

    p_reset = sub.add_parser("reset")
    p_reset.add_argument("path")
    p_reset.add_argument("--scope", default="compass")
    p_reset.add_argument("--confirm", action="store_true")

    p_te = sub.add_parser("theory-enter")
    p_te.add_argument("path")

    p_tf = sub.add_parser("theory-freeze")
    p_tf.add_argument("path")

    p_sh = sub.add_parser("setup-hooks")
    p_sh.add_argument("path")
    p_sh.add_argument("--write", action="store_true")

    args = parser.parse_args()
    {
        "status": cmd_status,
        "check": cmd_check,
        "update": cmd_update,
        "interview": cmd_interview,
        "reset": cmd_reset,
        "theory-enter": cmd_theory_enter,
        "theory-freeze": cmd_theory_freeze,
        "setup-hooks": cmd_setup_hooks,
    }[args.command](args)


if __name__ == "__main__":
    main()

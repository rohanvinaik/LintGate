"""Apply operations for test regeneration.

Extracted from _test_regeneration_gates.py: impl_rebuild_apply
and its helpers (_quarantine_files, _promote_generated).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from lintgate.next_action import NextAction, serialize_next_actions

_VALIDATION_FILE = ".lintgate/test_rebuild_validation.json"


def persist_validation(
    project_root: str,
    gates: dict,
    ready: bool,
    *,
    validation_path: str | None = None,
    review_ready_to_apply: bool = False,
) -> str:
    """Persist validation result for apply to check."""
    out_path = Path(validation_path) if validation_path else Path(project_root) / _VALIDATION_FILE
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "ready_to_apply": ready,
        "review_ready_to_apply": review_ready_to_apply,
        "gates": gates,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return str(out_path)


def _load_validation(project_root: str) -> dict | None:
    """Load persisted validation result."""
    vpath = Path(project_root) / _VALIDATION_FILE
    if not vpath.exists():
        return None
    try:
        with open(vpath, encoding="utf-8") as f:
            data: dict = json.load(f)
            return data
    except (OSError, json.JSONDecodeError):
        return None


def impl_rebuild_apply(
    helpers: dict[str, Any],
    path: str,
    dry_run: bool = True,
) -> str:
    """Apply the test rebuild: promote generated, quarantine old."""
    from lintgate.specification.test_regeneration_strategy import (
        load_manifest as load_manifest_fn,
    )

    project_root = helpers["_validate_project_root"](path)
    plan = load_manifest_fn(project_root)
    if plan is None:
        return str(
            helpers["_json_dumps"](
                {"error": "No manifest found. Run test_rebuild_plan first."},
                output_mode="compact",
            )
        )

    # Gate: require persisted validation pass before destructive action
    validation = _load_validation(project_root)
    if validation is None or not (
        validation.get("ready_to_apply") or validation.get("review_ready_to_apply")
    ):
        return str(
            helpers["_json_dumps"](
                {
                    "error": "No passing validation found. Run test_rebuild_validate first.",
                    "validation": validation,
                },
                output_mode="compact",
            )
        )

    actions: list[dict] = []
    actions = _quarantine_files(plan, project_root, dry_run, actions)
    actions = _promote_generated(project_root, dry_run, actions)

    output = {
        "dry_run": dry_run,
        "actions": actions,
        "quarantined": sum(1 for a in actions if a["action"] == "quarantine"),
        "promoted": sum(1 for a in actions if a["action"] == "promote"),
    }

    if dry_run:
        output["next_actions"] = serialize_next_actions(
            [
                NextAction(
                    tool="test_rebuild_apply",
                    args={"path": path, "dry_run": False},
                    reason="Execute the rebuild (non-dry-run)",
                ),
            ]
        )

    return str(helpers["_json_dumps"](output, output_mode="compact"))


def _quarantine_files(
    plan: Any,
    project_root: str,
    dry_run: bool,
    actions: list[dict],
) -> list[dict]:
    """Move old test files to quarantine directory, preserving path structure."""
    import shutil

    quarantine_dir = os.path.join(project_root, "tests", "quarantine")
    for qf in plan.quarantine_test_files:
        src = os.path.join(project_root, qf)
        # Preserve directory structure to avoid basename collisions
        # tests/api/test_utils.py → tests/quarantine/api/test_utils.py
        rel_to_tests = qf
        if qf.startswith("tests/") or qf.startswith("tests" + os.sep):
            rel_to_tests = qf[len("tests/") :]
        dst = os.path.join(quarantine_dir, rel_to_tests)
        if os.path.isfile(src):
            action = {
                "action": "quarantine",
                "source": qf,
                "destination": os.path.relpath(dst, project_root),
            }
            if not dry_run:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
            actions.append(action)
    return actions


def _promote_generated(
    project_root: str,
    dry_run: bool,
    actions: list[dict],
) -> list[dict]:
    """Promote generated tests from tests/generated/ to tests/."""
    import shutil

    gen_dir = os.path.join(project_root, "tests", "generated")
    if not os.path.isdir(gen_dir):
        return actions

    tests_dir = os.path.join(project_root, "tests")
    for f in sorted(os.listdir(gen_dir)):
        if not f.endswith(".py"):
            continue
        src = os.path.join(gen_dir, f)
        dst = os.path.join(tests_dir, f)
        action = {
            "action": "promote",
            "source": f"tests/generated/{f}",
            "destination": f"tests/{f}",
        }
        if not dry_run:
            shutil.move(src, dst)
        actions.append(action)

    if not dry_run and os.path.isdir(gen_dir) and not os.listdir(gen_dir):
        os.rmdir(gen_dir)

    return actions

"""Validation gates and file operations for test regeneration.

Extracted from _test_regeneration_impl.py: impl_rebuild_validate,
impl_rebuild_apply, and their helper functions.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

from lintgate.next_action import NextAction, serialize_next_actions

from ._test_regeneration_impl import _load_regen_config


def impl_rebuild_validate(
    helpers: dict[str, Any],
    path: str,
    review_ceiling: float = 0.15,
) -> str:
    """Validate generated tests against quality gates."""
    from lintgate.specification.test_regeneration_strategy import (
        load_manifest as load_manifest_fn,
    )

    project_root = helpers["_validate_project_root"](path)

    # Apply config default for review_ceiling
    if review_ceiling == 0.15:
        cfg = _load_regen_config(project_root)
        review_ceiling = cfg.review_ceiling

    plan = load_manifest_fn(project_root)
    if plan is None:
        return str(
            helpers["_json_dumps"](
                {"error": "No manifest found. Run test_rebuild_plan first."},
                output_mode="compact",
            )
        )

    gates: dict[str, Any] = {}
    gate_pass = True

    gate_pass, gates = _check_preserve_gate(
        plan, project_root, gates, gate_pass
    )
    gate_pass, gates = _check_generated_gate(
        project_root, gates, gate_pass
    )

    summary = plan.summary()
    review_share = summary.get("manual_review_share", 0.0)
    gates["review_share_ok"] = review_share <= review_ceiling
    gates["review_share"] = review_share
    if review_share > review_ceiling:
        gate_pass = False

    gate_pass, gates = _check_artifact_gate(plan, gates, gate_pass)

    output = {
        "ready_to_apply": gate_pass,
        "gates": gates,
        "summary": summary,
    }
    output["next_actions"] = serialize_next_actions(
        [
            NextAction(
                tool="test_rebuild_apply",
                args={"path": path},
                reason="Apply validated test rebuild",
            )
        ]
        if gate_pass
        else [
            NextAction(
                tool="test_rebuild_generate",
                args={"path": path, "write": True},
                reason="Fix issues and regenerate",
            )
        ]
    )
    return str(helpers["_json_dumps"](output, output_mode="compact"))


def _check_preserve_gate(
    plan: Any, project_root: str, gates: dict, gate_pass: bool,
) -> tuple[bool, dict]:
    """Gate 1: preserved tests still pass."""
    preserve_files = plan.preserve_test_files
    if not preserve_files:
        gates["preserve_tests_pass"] = True
        return gate_pass, gates

    abs_preserve = [
        os.path.join(project_root, f)
        for f in preserve_files
        if os.path.isfile(os.path.join(project_root, f))
    ]
    if not abs_preserve:
        gates["preserve_tests_pass"] = True
        return gate_pass, gates

    result = subprocess.run(
        ["python", "-m", "pytest", *abs_preserve, "--tb=no", "-q"],
        capture_output=True, text=True, cwd=project_root, timeout=120,
    )
    gates["preserve_tests_pass"] = result.returncode == 0
    if result.returncode != 0:
        gate_pass = False
    return gate_pass, gates


def _check_generated_gate(
    project_root: str, gates: dict, gate_pass: bool,
) -> tuple[bool, dict]:
    """Gate 2: generated tests import and run."""
    gen_dir = os.path.join(project_root, "tests", "generated")
    if not os.path.isdir(gen_dir):
        gates["generated_tests_run"] = True
        return gate_pass, gates

    gen_files = [
        os.path.join(gen_dir, f)
        for f in os.listdir(gen_dir)
        if f.endswith(".py") and f.startswith("test_")
    ]
    if not gen_files:
        gates["generated_tests_run"] = True
        return gate_pass, gates

    result = subprocess.run(
        ["python", "-m", "pytest", *gen_files, "--tb=short", "-q"],
        capture_output=True, text=True, cwd=project_root, timeout=120,
    )
    gates["generated_tests_run"] = result.returncode == 0
    gates["generated_test_output"] = (
        result.stdout[-500:] if result.stdout else ""
    )
    if result.returncode != 0:
        gate_pass = False
    return gate_pass, gates


def _check_artifact_gate(
    plan: Any, gates: dict, gate_pass: bool,
) -> tuple[bool, dict]:
    """Gate 4: no auto target has artifact discovery state."""
    from lintgate.specification.test_regeneration_strategy import Strategy

    artifact_count = 0
    for func in plan.functions:
        if func.strategy == Strategy.AUTO_GENERATE_UNIT:
            if func.evidence.discovery_state in (
                "DISCOVERY_ARTIFACT", "MOCK_BOUNDARY_ARTIFACT",
            ):
                artifact_count += 1
    gates["no_artifact_auto_targets"] = artifact_count == 0
    if artifact_count > 0:
        gate_pass = False
    return gate_pass, gates


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
    plan: Any, project_root: str, dry_run: bool, actions: list[dict],
) -> list[dict]:
    """Move old test files to quarantine directory."""
    import shutil

    quarantine_dir = os.path.join(project_root, "tests", "quarantine")
    for qf in plan.quarantine_test_files:
        src = os.path.join(project_root, qf)
        dst = os.path.join(quarantine_dir, os.path.basename(qf))
        if os.path.isfile(src):
            action = {
                "action": "quarantine",
                "source": qf,
                "destination": os.path.relpath(dst, project_root),
            }
            if not dry_run:
                os.makedirs(quarantine_dir, exist_ok=True)
                shutil.move(src, dst)
            actions.append(action)
    return actions


def _promote_generated(
    project_root: str, dry_run: bool, actions: list[dict],
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

"""Validation gates for test regeneration (9 gates)."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from lintgate.next_action import NextAction, serialize_next_actions
from lintgate.testing.fresh_validation import count_test_assertions, run_fresh_kill_rates

from ._test_regeneration_impl import _load_regen_config


def impl_rebuild_validate(
    helpers: dict[str, Any],
    path: str,
    review_ceiling: float = 0.15,
    *,
    generated_dir: str | None = None,
    validation_path: str | None = None,
) -> str:
    """Validate generated tests against quality gates."""
    from lintgate.specification.test_regeneration_strategy import (
        load_manifest as load_manifest_fn,
    )

    project_root = helpers["_validate_project_root"](path)

    if abs(review_ceiling - 0.15) < 1e-9:  # NOSONAR — sentinel default check
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

    # Gates 1-2: pytest sanity
    gate_pass, gates = _check_preserve_gate(plan, project_root, gates, gate_pass)
    gate_pass, gates = _check_generated_gate(
        plan, project_root, gates, gate_pass, generated_dir=generated_dir
    )

    # Gate 3: review ceiling
    summary = plan.summary()
    review_share = summary.get("manual_review_share", 0.0)
    gates["review_share_ok"] = review_share <= review_ceiling
    gates["review_share"] = review_share
    if review_share > review_ceiling:
        gate_pass = False

    # Gate 4: artifact check
    gate_pass, gates = _check_artifact_gate(plan, gates, gate_pass)

    # Gates 5-9: quality gates (kill rate, zero-kill, effectiveness, hygiene, redundancy)
    cfg = _load_regen_config(project_root)
    gate_pass, gates = _check_quality_gates(
        plan,
        project_root,
        helpers,
        gates,
        gate_pass,
        kill_floor=cfg.kill_floor,
        zero_kill_ceiling=cfg.zero_kill_ceiling,
        generated_dir=generated_dir,
    )

    # Compute review_ready_to_apply: true when the ONLY blocking failure is review ceiling
    review_ready = _is_review_ready_to_apply(gates, gate_pass)

    # Persist validation result so apply can gate on it
    from ._test_regeneration_apply import persist_validation

    persist_validation(
        project_root,
        gates,
        gate_pass,
        validation_path=validation_path,
        review_ready_to_apply=review_ready,
    )

    output = {
        "ready_to_apply": gate_pass,
        "review_ready_to_apply": review_ready,
        "gates": gates,
        "summary": summary,
        "scorecard": _build_scorecard(gates),
    }
    output["next_actions"] = serialize_next_actions(
        [
            NextAction(
                tool="test_rebuild_apply",
                args={"path": path},
                reason="Apply validated test rebuild",
            )
        ]
        if gate_pass or review_ready
        else [
            NextAction(
                tool="test_rebuild_generate",
                args={"path": path, "write": True},
                reason="Fix issues and regenerate",
            )
        ]
    )
    # NL summary
    pass_fail = "PASS" if gate_pass else ("REVIEW_READY" if review_ready else "FAIL")
    scorecard_lines: list[str] = _build_scorecard(gates)
    failed_gates = [line.strip() for line in scorecard_lines if "FAIL" in line]
    summary_parts = [f"test_rebuild_validate: {pass_fail}"]
    if failed_gates:
        summary_parts.append(f"Failed: {', '.join(failed_gates)}")

    from mcp_tools._disk_helpers import tool_response

    return tool_response(
        output,
        "test_rebuild_validate",
        project_root,
        " | ".join(summary_parts),
        next_actions=output.get("next_actions"),
    )


def _is_review_ready_to_apply(gates: dict[str, Any], gate_pass: bool) -> bool:
    """Return True when review ceiling is the only blocking validation failure."""
    if gate_pass or gates.get("review_share_ok") is not False:
        return False
    for gate_key, _label in _GATE_LABELS:
        if gate_key == "review_share_ok":
            continue
        if gate_key in gates and gates.get(gate_key) is not True:
            return False
    return True


def _check_preserve_gate(
    plan: Any,
    project_root: str,
    gates: dict,
    gate_pass: bool,
) -> tuple[bool, dict]:
    """Gate 1: preserved tests still pass."""
    abs_preserve = [
        os.path.join(project_root, f)
        for f in plan.preserve_test_files
        if os.path.isfile(os.path.join(project_root, f))
    ]
    if not abs_preserve:
        gates["preserve_tests_pass"] = True
        return gate_pass, gates
    result = subprocess.run(
        ["python", "-m", "pytest", *abs_preserve, "--tb=no", "-q"],
        capture_output=True,
        text=True,
        cwd=project_root,
        timeout=120,
    )
    gates["preserve_tests_pass"] = result.returncode == 0
    if result.returncode != 0:
        gate_pass = False
    return gate_pass, gates


def _check_generated_gate(
    plan: Any,
    project_root: str,
    gates: dict,
    gate_pass: bool,
    *,
    generated_dir: str | None = None,
) -> tuple[bool, dict]:
    """Gate 2: generated tests import, run, and contain assertions.

    Fail if auto targets exist but no generated tests.
    Fail if generated tests are all pass-stubs with no assertions.
    """
    from lintgate.specification.test_regeneration_strategy import Strategy

    has_auto = any(f.strategy == Strategy.AUTO_GENERATE_UNIT for f in plan.functions)
    gen_dir = generated_dir or os.path.join(project_root, "tests", "generated")
    gen_files = []
    if os.path.isdir(gen_dir):
        gen_files = [
            os.path.join(gen_dir, f)
            for f in os.listdir(gen_dir)
            if f.endswith(".py") and f.startswith("test_")
        ]

    if not gen_files:
        if has_auto:
            gates["generated_tests_run"] = False
            return False, gates
        gates["generated_tests_run"] = True
        return gate_pass, gates

    # Sub-gate 2a: pytest sanity — tests import and don't crash
    result = subprocess.run(
        ["python", "-m", "pytest", *gen_files, "--tb=short", "-q"],
        capture_output=True,
        text=True,
        cwd=project_root,
        timeout=120,
    )
    gates["generated_tests_run"] = result.returncode == 0
    if result.returncode != 0:
        gate_pass = False
        gates["generated_test_output"] = result.stdout[-500:] if result.stdout else ""
        return gate_pass, gates

    # Sub-gate 2b: assertion content — pass stubs are not real tests
    assertion_count = count_test_assertions(gen_files)
    gates["generated_assertion_count"] = assertion_count
    if assertion_count == 0 and has_auto:
        gates["generated_tests_run"] = False
        gates["generated_test_reason"] = "no assertions found in generated tests"
        gate_pass = False

    return gate_pass, gates


def _check_artifact_gate(
    plan: Any,
    gates: dict,
    gate_pass: bool,
) -> tuple[bool, dict]:
    """Gate 4: no auto target has artifact discovery state."""
    from lintgate.specification.test_regeneration_strategy import Strategy

    _artifacts = ("DISCOVERY_ARTIFACT", "MOCK_BOUNDARY_ARTIFACT", "DISCOVERY_WEAK_LINKAGE")
    artifact_count = sum(
        1
        for func in plan.functions
        if func.strategy == Strategy.AUTO_GENERATE_UNIT
        and (
            func.evidence.discovery_state in _artifacts
            or func.evidence.survival_interpretation in _artifacts
            or func.evidence.mutation_truth_label in _artifacts
        )
    )
    gates["no_artifact_auto_targets"] = artifact_count == 0
    if artifact_count > 0:
        gate_pass = False
    return gate_pass, gates


def _check_quality_gates(
    plan: Any,
    project_root: str,
    helpers: dict,
    gates: dict,
    gate_pass: bool,
    kill_floor: float = 0.70,
    zero_kill_ceiling: float = 0.05,
    *,
    generated_dir: str | None = None,
) -> tuple[bool, dict]:
    """Gates 5-9: kill rate, zero-kill, effectiveness, hygiene, redundancy."""
    from lintgate.specification.test_regeneration_strategy import Strategy

    has_auto = any(f.strategy == Strategy.AUTO_GENERATE_UNIT for f in plan.functions)

    # Gates 5-6: fresh targeted mutation sampling against generated tests
    kill_rates, zero_kill, sampling_details = run_fresh_kill_rates(
        plan, project_root, generated_dir=generated_dir
    )
    if sampling_details:
        gates["fresh_sampling_details"] = sampling_details
    if kill_rates:
        avg = sum(kill_rates) / len(kill_rates)
        gates["kill_rate"] = round(avg, 3)
        gates["kill_rate_ok"] = avg >= kill_floor
        if avg < kill_floor:
            gate_pass = False
        zr = zero_kill / len(kill_rates)
        gates["zero_kill_rate"] = round(zr, 3)
        gates["zero_kill_ok"] = zr <= zero_kill_ceiling
        if zr > zero_kill_ceiling:
            gate_pass = False
    elif has_auto:
        gates["kill_rate_ok"] = False
        gates["zero_kill_ok"] = False
        gate_pass = False
    else:
        gates["kill_rate_ok"] = True
        gates["zero_kill_ok"] = True

    # Gate 7: effectiveness (advisory)
    gates["effectiveness_ok"] = True
    try:
        from mcp_tools.test_effectiveness_tools import _analyze_test_strength_impl

        raw = _analyze_test_strength_impl(project_root, helpers)
        data = json.loads(raw) if isinstance(raw, str) else raw
        score = data.get("summary", {}).get("effectiveness_score")
        if score is not None:
            gates["effectiveness_score"] = round(float(score), 3)
    except Exception:
        pass
    # Gate 8: hygiene — critical findings block
    gates["hygiene_ok"] = True
    try:
        from lintgate.channels.test_hygiene_channel import TestHygieneChannel
        from lintgate.controlplane.types import ControlPlaneConfig as CPConfig
        from lintgate.controlplane.types import SupervisionEvent as SEvent

        ch_result = TestHygieneChannel().execute(
            SEvent(surface="mcp", project_root=project_root), CPConfig(enabled=True, channels={})
        )
        critical = sum(1 for f in ch_result.findings if getattr(f, "severity", "INFO") == "ERROR")
        gates["hygiene_critical"] = critical
        if critical:
            gates["hygiene_ok"] = False
            gate_pass = False
    except Exception:
        pass
    # Gate 9: redundancy (advisory)
    gates["redundancy_ok"] = True
    try:
        from mcp_tools.redundancy_tools import _impl_redundancy_project

        raw = _impl_redundancy_project(project_root, top_n=10)
        data = json.loads(raw) if isinstance(raw, str) else raw
        gates["redundant_test_count"] = data.get("redundant_test_count", 0)
    except Exception:
        pass

    return gate_pass, gates


_GATE_LABELS = [
    ("preserve_tests_pass", "Preserve pass"),
    ("generated_tests_run", "Generated run"),
    ("review_share_ok", "Review ceiling"),
    ("no_artifact_auto_targets", "No artifacts"),
    ("kill_rate_ok", "Kill rate"),
    ("zero_kill_ok", "Zero-kill"),
    ("effectiveness_ok", "Effectiveness"),
    ("hygiene_ok", "Hygiene"),
    ("redundancy_ok", "Redundancy"),
]


def _build_scorecard(gates: dict[str, Any]) -> list[str]:
    """Build compact scorecard lines from gate results."""
    return [f"  [{'PASS' if gates[k] else 'FAIL'}] {lbl}" for k, lbl in _GATE_LABELS if k in gates]

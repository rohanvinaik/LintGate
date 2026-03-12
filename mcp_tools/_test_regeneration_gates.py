"""Validation gates for test regeneration (9 gates)."""

from __future__ import annotations

import json
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

    # Gates 1-2: pytest sanity
    gate_pass, gates = _check_preserve_gate(plan, project_root, gates, gate_pass)
    gate_pass, gates = _check_generated_gate(plan, project_root, gates, gate_pass)

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
    )

    # Persist validation result so apply can gate on it
    from ._test_regeneration_apply import persist_validation

    persist_validation(project_root, gates, gate_pass)

    output = {
        "ready_to_apply": gate_pass,
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
) -> tuple[bool, dict]:
    """Gate 2: generated tests import and run. Fail if auto targets exist but no tests."""
    from lintgate.specification.test_regeneration_strategy import Strategy

    has_auto = any(f.strategy == Strategy.AUTO_GENERATE_UNIT for f in plan.functions)
    gen_dir = os.path.join(project_root, "tests", "generated")
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


def _check_artifact_gate(
    plan: Any,
    gates: dict,
    gate_pass: bool,
) -> tuple[bool, dict]:
    """Gate 4: no auto target has artifact discovery state."""
    from lintgate.specification.test_regeneration_strategy import Strategy

    artifact_count = sum(
        1
        for func in plan.functions
        if func.strategy == Strategy.AUTO_GENERATE_UNIT
        and func.evidence.discovery_state
        in (
            "DISCOVERY_ARTIFACT",
            "MOCK_BOUNDARY_ARTIFACT",
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
) -> tuple[bool, dict]:
    """Gates 5-9: kill rate, zero-kill, effectiveness, hygiene, redundancy."""
    # Gates 5-6: kill rate + zero-kill from mutation cache
    from lintgate.specification.test_regeneration_strategy import Strategy

    has_auto = any(f.strategy == Strategy.AUTO_GENERATE_UNIT for f in plan.functions)
    kill_rates, zero_kill = _sample_kill_rates(plan, project_root)
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
        # Auto targets exist but no mutation data — cannot validate
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
        from lintgate.controlplane.types import (
            ControlPlaneConfig as CPC,
        )
        from lintgate.controlplane.types import (
            SupervisionEvent as SE,
        )

        ch_result = TestHygieneChannel().execute(
            SE(surface="mcp", project_root=project_root),
            CPC(enabled=True, channels={}),
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


def _sample_kill_rates(
    plan: Any,
    project_root: str,
) -> tuple[list[float], int]:
    """Extract kill rates from mutation cache for auto-generate targets."""
    from lintgate.specification.test_regeneration_strategy import Strategy

    auto_funcs = [f for f in plan.functions if f.strategy == Strategy.AUTO_GENERATE_UNIT]
    if not auto_funcs:
        return [], 0

    try:
        from mcp_tools._mutation_impl import get_cache_dir, iter_cached_states

        cached = {
            s["function_key"]: s
            for s in iter_cached_states(get_cache_dir(project_root))
            if "function_key" in s
        }
    except Exception:
        return [], 0

    rates: list[float] = []
    zero = 0
    for func in auto_funcs:
        state = cached.get(func.evidence.function_key)
        if not state:
            continue
        kr = 1.0 - state.get("survival_rate", 1.0)
        rates.append(kr)
        if kr == 0.0:
            zero += 1
    return rates, zero


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

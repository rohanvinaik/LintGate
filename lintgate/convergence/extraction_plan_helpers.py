"""Warning generation, impact estimation, batch plans, and helpers for extraction."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .extraction_plan import ExtractionPlan

from .evidence import ConvergenceResult, LensEvidence, LensKind

# ── Warning Generation ─────────────────────────────────────────────────


def _warn_fan_in(ev: LensEvidence) -> str | None:
    fan_in = ev.raw.get("fan_in", 0)
    if fan_in >= 5:
        return f"Warning: {fan_in} modules import this — extraction will require updating all importers"
    return None


def _warn_cochange(ev: LensEvidence) -> str | None:
    strength = ev.raw.get("coupling_strength", 0.0)
    if strength > 0.6:
        coupled_file = _extract_coupled_file(ev)
        return f"Warning: high co-change coupling ({strength:.2f}) with {coupled_file} — consider extracting together"
    return None


def _warn_algebraic(ev: LensEvidence) -> str | None:
    if "unsafe" in ev.detail.lower():
        return f"Warning: algebraic analysis flags unsafe extraction — {ev.detail}"
    return None


def _warn_import_tracing(ev: LensEvidence) -> str | None:
    if "io" in ev.detail.lower():
        return "Warning: module-level IO detected — extraction may change initialization order"
    return None


_WARNING_HANDLERS: dict[LensKind, Any] = {
    LensKind.FAN_IN: _warn_fan_in,
    LensKind.COCHANGE: _warn_cochange,
    LensKind.ALGEBRAIC: _warn_algebraic,
    LensKind.IMPORT_TRACING: _warn_import_tracing,
}


def _generate_warnings(candidate: ConvergenceResult) -> list[str]:
    """Generate warnings from opposing evidence."""
    warnings: list[str] = []
    for ev in candidate.evidence:
        if ev.signal != "oppose":
            continue
        handler = _WARNING_HANDLERS.get(ev.lens)
        if handler:
            msg = handler(ev)
            if msg:
                warnings.append(msg)
    return warnings


# ── Impact Estimation ──────────────────────────────────────────────────


def _compute_estimated_impact(
    plan: ExtractionPlan,
    candidate: ConvergenceResult,
    prescriptions: list[dict],
    evidence_by_lens: dict[LensKind, list[LensEvidence]],
) -> dict[str, Any]:
    """Compute estimated impact metrics for the extraction plan."""
    impact: dict[str, Any] = {}

    # CC reduction from prescriptions
    total_cc_reduction = sum(
        p.get("expected_delta", {}).get("cc_reduction", 0) for p in prescriptions
    )
    impact["CC_reduction"] = total_cc_reduction

    # Fan-in change
    fan_in_count = _get_fan_in_count(evidence_by_lens)
    impact["fan_in_change"] = -fan_in_count if fan_in_count > 0 else 0

    # Test count needed
    handler_steps = [s for s in plan.steps if s.action == "extract_handler"]
    block_steps = [s for s in plan.steps if s.action == "create_function"]
    extraction_count = len(handler_steps) or len(block_steps) or 1
    impact["test_count_needed"] = extraction_count

    # Line count delta (handler extraction adds ~12 lines per handler for explicit params)
    if handler_steps:
        avg_lines_per_handler = 12
        line_delta = len(handler_steps) * avg_lines_per_handler
        impact["line_count_delta"] = line_delta
        impact["line_count_explanation"] = (
            f"Explicit parameter passing adds ~{avg_lines_per_handler} lines per extracted handler"
        )
    elif prescriptions:
        # Block extraction: net neutral or slight reduction
        total_lines = sum(_line_count_from_prescription(p) for p in prescriptions)
        # New function signatures add ~3 lines each, but code moves
        impact["line_count_delta"] = len(prescriptions) * 3
        impact["line_count_explanation"] = (
            f"Function signatures add ~3 lines each; "
            f"{total_lines} lines of body code moves (net neutral)"
        )

    return impact


# ── Batch Extraction Plans ─────────────────────────────────────────────


def build_batch_extraction_plan(
    candidates: list[ConvergenceResult],
    ast_context: ast.Module | None = None,
    source_file: str = "",
) -> list[ExtractionPlan]:
    """Build extraction plans for multiple convergence candidates.

    When candidates share the same source file, plans are coordinated
    to avoid conflicting line ranges.

    Returns:
        List of ExtractionPlan, one per candidate.
    """
    plans: list[ExtractionPlan] = []
    for candidate in candidates:
        from .extraction_plan import build_extraction_plan

        plan = build_extraction_plan(candidate, ast_context, source_file)
        plans.append(plan)
    return plans


# ── Helpers ────────────────────────────────────────────────────────────


def _infer_source_file(candidate: ConvergenceResult) -> str:
    """Infer source file from the convergence target."""
    target = candidate.target
    if "::" in target:
        return target.split("::")[0]
    return target


def _group_evidence_by_lens(
    evidence: list[LensEvidence],
) -> dict[LensKind, list[LensEvidence]]:
    """Group evidence items by their lens kind."""
    by_lens: dict[LensKind, list[LensEvidence]] = {}
    for ev in evidence:
        by_lens.setdefault(ev.lens, []).append(ev)
    return by_lens


def _extract_prescriptions(evidence: list[LensEvidence]) -> list[dict]:
    """Extract prescription-like data from DEP_CLUSTERING evidence."""
    prescriptions: list[dict] = []
    for ev in evidence:
        if ev.lens == LensKind.DEP_CLUSTERING:
            raw = ev.raw
            # Try to extract structured data
            p: dict[str, Any] = {
                "target": ev.target,
                "detail": ev.detail,
            }
            # Check if raw contains structured prescription data
            for key in (
                "proposed_name",
                "inputs",
                "outputs",
                "lines",
                "expected_delta",
                "basis",
                "kind",
                "action",
            ):
                if key in raw:
                    p[key] = raw[key]
            prescriptions.append(p)
    return prescriptions


def _is_handler_prescription(p: dict) -> bool:
    """Check if a prescription is for handler extraction."""
    kind = p.get("kind", "")
    basis = p.get("basis", [])
    action = p.get("action", "")
    return (
        kind == "decompose_register"
        or "nested_handler" in basis
        or "extract nested handler" in action.lower()
        or "extract_handler" in action.lower()
    )


def _extract_handler_name(p: dict) -> str:
    """Extract the handler name from a handler prescription."""
    action = p.get("action", "")
    # Try to parse "Extract nested handler `name`" pattern
    if "`" in action:
        parts = action.split("`")
        if len(parts) >= 2:
            return str(parts[1])
    target = p.get("target", "")
    if "::" in target:
        return str(target.split("::")[-1])
    return ""


def _get_fan_in_count(
    evidence_by_lens: dict[LensKind, list[LensEvidence]],
) -> int:
    """Get fan-in count from evidence."""
    fan_in_evidence = evidence_by_lens.get(LensKind.FAN_IN, [])
    for ev in fan_in_evidence:
        if ev.signal == "oppose":
            return int(ev.raw.get("fan_in", 0))
    return 0


def _get_importer_list(
    evidence_by_lens: dict[LensKind, list[LensEvidence]],
) -> list[str]:
    """Get list of importers from fan-in evidence."""
    fan_in_evidence = evidence_by_lens.get(LensKind.FAN_IN, [])
    importers: list[str] = []
    for ev in fan_in_evidence:
        if ev.signal == "oppose":
            raw_importers = ev.raw.get("importers", [])
            importers.extend(raw_importers)
    return importers


def _extract_coupled_file(ev: LensEvidence) -> str:
    """Extract the coupled file name from co-change evidence."""
    raw = ev.raw
    file_a = raw.get("file_a", "")
    file_b = raw.get("file_b", "")
    target = ev.target
    if file_a and file_b:
        return str(file_b) if file_a in target else str(file_a)
    # Parse from detail
    detail = ev.detail
    if "with " in detail:
        return detail.split("with ")[-1].split(" ")[0]
    return "unknown"


def _infer_param_types(
    inputs: list[str],
    ast_context: ast.Module | None,
) -> dict[str, str]:
    """Infer parameter types from AST annotations if available."""
    if not ast_context or not inputs:
        return {}

    type_map: dict[str, str] = {}
    for node in ast.walk(ast_context):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in inputs and node.annotation:
                type_map[node.target.id] = ast.unparse(node.annotation)
        elif isinstance(node, ast.arg) and node.arg in inputs and node.annotation:
            type_map[node.arg] = ast.unparse(node.annotation)
    return type_map


def _infer_return_type(
    outputs: list[str],
    ast_context: ast.Module | None,
) -> str:
    """Infer return type from outputs."""
    if not outputs:
        return "None"
    if len(outputs) == 1:
        return "Any"
    return f"tuple[{', '.join(['Any'] * len(outputs))}]"


def _suggest_module_name(candidate: ConvergenceResult) -> str:
    """Suggest a module name for file-level splits."""
    target = candidate.target
    if candidate.split_proposals:
        first = candidate.split_proposals[0]
        if isinstance(first, dict) and "module_name" in first:
            return str(first["module_name"])
    # Default: append _extracted to base name
    base = target.rsplit("/", 1)[-1] if "/" in target else target
    if base.endswith(".py"):
        base = base[:-3]
    return f"{base}_extracted.py"


def _reorder_steps(plan: ExtractionPlan) -> None:
    """Re-order steps to maintain canonical ordering.

    Canonical order: create_function → extract_body → extract_handler →
    create_module → update_callers → update_imports → migrate_tests
    """
    action_priority = {
        "create_function": 1,
        "extract_body": 2,
        "extract_handler": 3,
        "create_module": 4,
        "update_callers": 5,
        "update_imports": 6,
        "migrate_tests": 7,
    }
    plan.steps.sort(key=lambda s: (action_priority.get(s.action, 99), s.order))
    for i, step in enumerate(plan.steps, 1):
        step.order = i


def _line_count_from_prescription(p: dict) -> int:
    """Estimate line count from a prescription's lines range."""
    lines = p.get("lines")
    if lines and len(lines) == 2:
        return int(lines[1]) - int(lines[0]) + 1
    return 0

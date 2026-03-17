"""Mutation-targeted tests for _prescriptive_helpers and _prescriptive_impl.

Targets VALUE and SWAP surviving mutants identified by platonic_sweep.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from mcp_tools._prescriptive_helpers import _render_generation_prompt, _render_repair_prompt

# ── _render_generation_prompt VALUE tests ─────────────────────────


def test_render_generation_prompt_empty_constraints():
    result = _render_generation_prompt("mod::func", [])
    assert "mod::func" in result
    assert "Forbidden" not in result
    assert "Required" not in result


def test_render_generation_prompt_must_use_constraint():
    constraints = [{"description": "Use dataclass", "constraint_type": "must_use", "priority": 1}]
    result = _render_generation_prompt("mod::func", constraints)
    assert "MUST: Use dataclass" in result
    assert "### Required" in result


def test_render_generation_prompt_must_not_use_constraint():
    constraints = [
        {"description": "No global state", "constraint_type": "must_not_use", "priority": 1}
    ]
    result = _render_generation_prompt("mod::func", constraints)
    assert "MUST NOT: No global state" in result
    assert "### Forbidden" in result


def test_render_generation_prompt_pattern_constraint():
    constraints = [{"description": "Return dict", "constraint_type": "pattern", "priority": 3}]
    result = _render_generation_prompt("mod::func", constraints)
    assert "- Return dict" in result
    assert "### Patterns" in result


def test_render_generation_prompt_sorted_by_priority():
    constraints = [
        {"description": "Low priority", "constraint_type": "pattern", "priority": 9},
        {"description": "High priority", "constraint_type": "must_use", "priority": 1},
    ]
    result = _render_generation_prompt("mod::func", constraints)
    # High priority should appear before low priority
    high_pos = result.index("High priority")
    low_pos = result.index("Low priority")
    assert high_pos < low_pos


# ── _render_generation_prompt SWAP tests ──────────────────────────


def test_render_generation_prompt_different_targets_differ():
    constraints = [{"description": "Pure function", "constraint_type": "must_use", "priority": 1}]
    result_a = _render_generation_prompt("mod::func_a", constraints)
    result_b = _render_generation_prompt("mod::func_b", constraints)
    assert result_a != result_b


def test_render_generation_prompt_different_constraints_differ():
    c1 = [{"description": "Pure", "constraint_type": "must_use", "priority": 1}]
    c2 = [{"description": "Stateful", "constraint_type": "must_not_use", "priority": 1}]
    assert _render_generation_prompt("mod::f", c1) != _render_generation_prompt("mod::f", c2)


# ── _render_repair_prompt VALUE tests ─────────────────────────────


def _mock_spec(custom_hints=None):
    from lintgate.specification.prescriptive.spec import Invariant, Predicate, PredicateOp

    spec = MagicMock()
    if custom_hints:
        spec.invariants = [
            Invariant(
                name=f"hint_{i}",
                description=h,
                source="test",
                confidence=1.0,
                kind="behavioral",
                predicate=Predicate(op=PredicateOp.CUSTOM),
            )
            for i, h in enumerate(custom_hints)
        ]
    else:
        spec.invariants = []
    return spec


def test_render_repair_prompt_with_stub():
    spec = _mock_spec()
    targets = MagicMock()
    targets.implementation_stub = "def func(x: int) -> str:\n    pass"
    targets.generation_constraints = []
    gate = MagicMock()
    gate.reasons = []

    result = _render_repair_prompt(spec, targets, gate, None)
    assert "def func(x: int) -> str:" in result
    assert "**Signature:**" in result


def test_render_repair_prompt_with_constraints():
    spec = _mock_spec()
    targets = MagicMock()
    targets.implementation_stub = ""
    targets.generation_constraints = [
        {"description": "Must be pure", "constraint_type": "must_use", "priority": 1},
        {"description": "No IO", "constraint_type": "must_not_use", "priority": 2},
    ]
    gate = MagicMock()
    gate.reasons = []

    result = _render_repair_prompt(spec, targets, gate, None)
    assert "MUST: Must be pure" in result
    assert "MUST NOT: No IO" in result


def test_render_repair_prompt_with_semantic_hints():
    spec = _mock_spec(custom_hints=["Build reverse call graph", "Group by module"])
    targets = MagicMock()
    targets.implementation_stub = ""
    targets.generation_constraints = []
    gate = MagicMock()
    gate.reasons = []

    result = _render_repair_prompt(spec, targets, gate, None)
    assert "Build reverse call graph" in result
    assert "### Semantic Hints" in result


def test_render_repair_prompt_with_gate_blockers():
    spec = _mock_spec()
    targets = MagicMock()
    targets.implementation_stub = ""
    targets.generation_constraints = []
    gate = MagicMock()
    gate.reasons = ["Not pure", "Too many params"]

    result = _render_repair_prompt(spec, targets, gate, None)
    assert "Not pure" in result
    assert "### Gate Blockers" in result


def test_render_repair_prompt_with_synthesis_failure():
    spec = _mock_spec()
    targets = MagicMock()
    targets.implementation_stub = ""
    targets.generation_constraints = []
    gate = MagicMock()
    gate.reasons = []
    synth = MagicMock()
    synth.success = False
    synth.failure_reason = "No pattern match"
    synth.body = "return {}"

    result = _render_repair_prompt(spec, targets, gate, synth)
    assert "No pattern match" in result
    assert "return {}" in result


# ── _render_repair_prompt SWAP tests ──────────────────────────────


def test_render_repair_prompt_different_specs_differ():
    spec_a = _mock_spec(custom_hints=["Hint A"])
    spec_b = _mock_spec(custom_hints=["Hint B"])
    targets = MagicMock()
    targets.implementation_stub = ""
    targets.generation_constraints = []
    gate = MagicMock()
    gate.reasons = []

    result_a = _render_repair_prompt(spec_a, targets, gate, None)
    result_b = _render_repair_prompt(spec_b, targets, gate, None)
    assert result_a != result_b

"""Test prescription engine — risk-prioritized prescriptions with expanded taxonomy.

Generates actionable test prescriptions based on specification analysis,
phase-aware selection, and orthogonal-array hinting for decision tables.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import FunctionSpecification

_BAND_SORT_ORDER: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2}


@dataclass
class TestPrescription:
    """A single test prescription for a function."""

    function_key: str = ""
    prescription_kind: str = ""
    description: str = ""
    estimated_info_gain: float = 0.0
    priority: int = 0
    priority_band: str = "P2"
    uncovered_dimension: str = ""
    suggested_assertion: str = ""
    targets_count: int | None = None
    regression_relevant: bool = False


def prescribe(
    func_spec: FunctionSpecification,
    max_prescriptions: int = 10,
    regression_mode: bool = False,
) -> list[TestPrescription]:
    """Generate risk-prioritized test prescriptions for a function."""
    phase = func_spec.core.phase
    sigma = func_spec.core.estimated_sigma
    assertion_count = func_spec.traceability.assertion_count
    history = set(func_spec.traceability.prescription_history)

    prescriptions: list[TestPrescription] = []

    if phase == "complete" and not regression_mode:
        return []

    generators = _phase_generators(phase, regression_mode)
    for gen_fn in generators:
        gen_fn(func_spec, sigma, assertion_count, history, prescriptions)

    # Sort: P0 first, then by info_gain descending
    prescriptions.sort(
        key=lambda p: (_BAND_SORT_ORDER.get(p.priority_band, 3), -p.estimated_info_gain)
    )

    return prescriptions[:max_prescriptions]


def _phase_generators(phase: str, regression_mode: bool) -> list:
    """Select prescription generators based on phase."""
    if regression_mode:
        return [_gen_regression]

    if phase == "bulk":
        return [_gen_exact_value, _gen_equivalence]
    if phase == "transition":
        return [_gen_boundary, _gen_cause_effect, _gen_decision_table]
    if phase == "tail":
        return [_gen_property, _gen_cause_effect]
    return []


def _info_gain(sigma: int) -> float:
    """Compute information gain per test: log2(sigma / max(sigma-1, 1))."""
    if sigma <= 1:
        return 1.0
    return math.log2(sigma / (sigma - 1))


# ── Prescription generators ─────────────────────────────────────────


def _gen_exact_value(
    fs: FunctionSpecification,
    sigma: int,
    assertion_count: int,
    history: set[str],
    out: list[TestPrescription],
) -> None:
    if "exact_value" in history:
        return
    gap = max(sigma - assertion_count, 0)
    if gap <= 0:
        return
    out.append(
        TestPrescription(
            function_key=fs.function_key,
            prescription_kind="exact_value",
            description=f"Add exact-value assertions for {fs.function_key} (gap: {gap})",
            estimated_info_gain=_info_gain(sigma) * 2,
            priority=1,
            priority_band=fs.risk.priority_band,
            uncovered_dimension="value correctness",
            suggested_assertion=f"assert {_short_name(fs.function_key)}(...) == expected",
        )
    )


def _gen_equivalence(
    fs: FunctionSpecification,
    sigma: int,
    assertion_count: int,
    history: set[str],
    out: list[TestPrescription],
) -> None:
    if "equivalence" in history:
        return
    partitions = fs.design_signals.equivalence_partitions
    if partitions <= 0:
        return
    out.append(
        TestPrescription(
            function_key=fs.function_key,
            prescription_kind="equivalence",
            description=f"Test {partitions} equivalence partitions for {_short_name(fs.function_key)}",
            estimated_info_gain=_info_gain(sigma),
            priority=2,
            priority_band=fs.risk.priority_band,
            uncovered_dimension="input class coverage",
            suggested_assertion="Test one representative from each input partition",
            targets_count=partitions,
        )
    )


def _gen_boundary(
    fs: FunctionSpecification,
    sigma: int,
    assertion_count: int,
    history: set[str],
    out: list[TestPrescription],
) -> None:
    if "boundary" in history:
        return
    bp = fs.design_signals.boundary_points
    if bp <= 0:
        return
    out.append(
        TestPrescription(
            function_key=fs.function_key,
            prescription_kind="boundary",
            description=f"Add BVA tests for {bp} boundary points in {_short_name(fs.function_key)}",
            estimated_info_gain=_info_gain(sigma) * 1.5,
            priority=1,
            priority_band=fs.risk.priority_band,
            uncovered_dimension="boundary behavior",
            suggested_assertion="Test at boundary-1, boundary, boundary+1",
            targets_count=bp,
        )
    )


def _gen_cause_effect(
    fs: FunctionSpecification,
    sigma: int,
    assertion_count: int,
    history: set[str],
    out: list[TestPrescription],
) -> None:
    if "cause_effect" in history:
        return
    links = fs.design_signals.predicate_effect_links
    if links <= 0:
        return
    out.append(
        TestPrescription(
            function_key=fs.function_key,
            prescription_kind="cause_effect",
            description=f"Test {links} predicate→effect edges in {_short_name(fs.function_key)}",
            estimated_info_gain=_info_gain(sigma),
            priority=2,
            priority_band=fs.risk.priority_band,
            uncovered_dimension="predicate-effect coverage",
            suggested_assertion="For each predicate, verify the expected effect",
        )
    )


def _gen_decision_table(
    fs: FunctionSpecification,
    sigma: int,
    assertion_count: int,
    history: set[str],
    out: list[TestPrescription],
) -> None:
    if "decision_table" in history:
        return
    rules = fs.design_signals.decision_rule_count
    if rules <= 4:
        return

    # Orthogonal-array hinting for large tables
    if rules > 16:
        covering = math.ceil(math.log2(rules)) + 1
        desc = (
            f"Decision table: {rules} rules in {_short_name(fs.function_key)}. "
            f"Full coverage requires {rules} tests; "
            f"pairwise covering array requires {covering}"
        )
    else:
        covering = rules
        desc = f"Test {rules} decision rules in {_short_name(fs.function_key)}"

    out.append(
        TestPrescription(
            function_key=fs.function_key,
            prescription_kind="decision_table",
            description=desc,
            estimated_info_gain=_info_gain(sigma) * 1.5,
            priority=2,
            priority_band=fs.risk.priority_band,
            uncovered_dimension="condition combination coverage",
            suggested_assertion=f"Test {covering} condition combinations",
        )
    )


def _gen_property(
    fs: FunctionSpecification,
    sigma: int,
    assertion_count: int,
    history: set[str],
    out: list[TestPrescription],
) -> None:
    if "property" in history:
        return
    if not fs.core.is_pure:
        return
    out.append(
        TestPrescription(
            function_key=fs.function_key,
            prescription_kind="property",
            description=f"Add property-based (Hypothesis) tests for pure {_short_name(fs.function_key)}",
            estimated_info_gain=_info_gain(sigma) * 2,
            priority=3,
            priority_band=fs.risk.priority_band,
            uncovered_dimension="algebraic properties",
            suggested_assertion="@given(st.from_type(...)) + invariant assertions",
        )
    )


def _gen_regression(
    fs: FunctionSpecification,
    sigma: int,
    assertion_count: int,
    history: set[str],
    out: list[TestPrescription],
) -> None:
    out.append(
        TestPrescription(
            function_key=fs.function_key,
            prescription_kind="regression",
            description=f"Re-test changed dimensions of {_short_name(fs.function_key)}",
            estimated_info_gain=_info_gain(sigma),
            priority=0,
            priority_band=fs.risk.priority_band,
            uncovered_dimension="recently changed",
            suggested_assertion="Re-verify behavior at changed code paths",
            regression_relevant=True,
        )
    )


def _short_name(func_key: str) -> str:
    """Extract short name from qualified key."""
    return func_key.split("::")[-1] if "::" in func_key else func_key

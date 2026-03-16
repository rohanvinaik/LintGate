"""Problem-class backend compilers for PrescriptiveSpec.

Three backends compile PrescriptiveSpec into test skeletons + generation constraints:
- PureBackend: algebraic contract compiler for pure/local functions
- StatefulBackend: state-machine skeleton compiler
- DistributedBackend: communicating state machines (protocol conformance)

Plus PrescriptiveAdapter: bridges CompilationTargets into existing mutation/spec APIs.
Plus SynthesisGateResult / WitnessRecord / executable-witness generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .spec import PrescriptiveSpec

from .adapter import PrescriptiveAdapter  # noqa: F401 — re-export
from .gate import (  # noqa: F401 — re-export + used by PureBackend.compile
    SynthesisGateResult,
    WitnessRecord,
    _build_synthesis_profile,
    _classify_return_type,
    _has_only_custom_predicates,
    check_synthesis_gate,
    generate_executable_witnesses,
)


# Lazy re-exports (kept for any names not covered above)
def __getattr__(name: str) -> Any:
    adapter_names = {"PrescriptiveAdapter"}
    gate_names = {
        "SynthesisGateResult",
        "WitnessRecord",
        "check_synthesis_gate",
        "generate_executable_witnesses",
        "_classify_return_type",
        "_build_synthesis_profile",
        "_has_only_custom_predicates",
    }
    if name in adapter_names:
        from . import adapter

        return getattr(adapter, name)
    if name in gate_names:
        from . import gate

        return getattr(gate, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ── CompilationTargets ────────────────────────────────────────────────


@dataclass
class CompilationTargets:
    """Output of backend compilation."""

    property_tests: list[dict[str, Any]] = field(default_factory=list)
    scenario_tests: list[dict[str, Any]] = field(default_factory=list)
    expected_kill_set: dict[str, bool] = field(default_factory=dict)
    compass_gate_assertions: list[dict[str, Any]] = field(default_factory=list)
    generation_constraints: list[dict[str, Any]] = field(default_factory=list)

    # Implementation stub artifacts (populated by PureBackend)
    implementation_stub: str = ""
    docstring_stub: str = ""
    body_slot: str = ""
    synthesis_profile: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "property_tests": self.property_tests,
            "scenario_tests": self.scenario_tests,
            "expected_kill_set": self.expected_kill_set,
            "compass_gate_assertions": self.compass_gate_assertions,
            "generation_constraints": self.generation_constraints,
            "implementation_stub": self.implementation_stub,
            "docstring_stub": self.docstring_stub,
            "body_slot": self.body_slot,
            "synthesis_profile": self.synthesis_profile,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompilationTargets:
        return cls(
            property_tests=data.get("property_tests", []),
            scenario_tests=data.get("scenario_tests", []),
            expected_kill_set=data.get("expected_kill_set", {}),
            compass_gate_assertions=data.get("compass_gate_assertions", []),
            generation_constraints=data.get("generation_constraints", []),
            implementation_stub=data.get("implementation_stub", ""),
            docstring_stub=data.get("docstring_stub", ""),
            body_slot=data.get("body_slot", ""),
            synthesis_profile=data.get("synthesis_profile", {}),
        )


# ── Pure Backend ──────────────────────────────────────────────────────


class PureBackend:
    """Algebraic contract compiler for pure/local functions."""

    def compile(self, spec: PrescriptiveSpec) -> CompilationTargets:
        targets = CompilationTargets()

        # Algebraic laws → Hypothesis property skeletons
        for law in spec.algebraic_laws:
            name = law.get("name", law.get("property_name", "unnamed"))
            targets.property_tests.append(
                {
                    "name": f"test_property_{name}",
                    "type": "hypothesis",
                    "law": law,
                    "skeleton": (
                        f"@given(st.from_type({spec.return_type or 'Any'}))\n"
                        f"def test_property_{name}(x):\n"
                        f"    # Verify algebraic law: {name}\n"
                        f"    assert ...  # TODO: fill in"
                    ),
                    "target_function": spec.target_key,
                }
            )

        # Invariants → exact-value test skeletons
        for inv in spec.invariants:
            targets.scenario_tests.append(
                {
                    "name": f"test_invariant_{inv.name}",
                    "type": "exact_value",
                    "invariant": inv.to_dict(),
                    "skeleton": (
                        f"def test_invariant_{inv.name}():\n"
                        f"    # Invariant: {inv.description}\n"
                        f"    result = {_func_call(spec)}\n"
                        f"    assert ...  # TODO: verify {inv.description}"
                    ),
                    "target_function": spec.target_key,
                }
            )

        # Forbidden behaviors → negative test skeletons
        for i, fb in enumerate(spec.forbidden_behaviors):
            targets.scenario_tests.append(
                {
                    "name": f"test_forbidden_{i}",
                    "type": "negative",
                    "forbidden": fb.to_dict(),
                    "skeleton": (
                        f"def test_forbidden_{i}():\n"
                        f"    # Must NOT: {fb.description}\n"
                        f"    # Severity: {fb.severity}\n"
                        f"    ...  # TODO: verify absence of forbidden behavior"
                    ),
                    "target_function": spec.target_key,
                }
            )

        # Refinement obligations → expected mutation kill set
        for ro in spec.refinement_obligations:
            targets.expected_kill_set[ro.category] = ro.expected_kill

        # Generation constraints
        targets.generation_constraints = [gc.to_dict() for gc in spec.generation_constraints]

        # Compass gate assertions from invariants
        for inv in spec.invariants:
            targets.compass_gate_assertions.append(
                {
                    "invariant": inv.name,
                    "description": inv.description,
                    "kind": inv.kind,
                }
            )

        # Implementation stub artifacts
        targets.implementation_stub = _build_signature(spec)
        targets.docstring_stub = _build_docstring(spec)
        targets.body_slot = "    pass  # TODO: implement"
        targets.synthesis_profile = _build_synthesis_profile(spec)

        return targets


# ── Stateful Backend ──────────────────────────────────────────────────


class StatefulBackend:
    """State-machine skeleton compiler."""

    def compile(self, spec: PrescriptiveSpec) -> CompilationTargets:
        targets = CompilationTargets()

        # One test per allowed transition verifying postconditions
        for trans in spec.allowed_transitions:
            targets.scenario_tests.append(
                {
                    "name": f"test_transition_{trans.name}",
                    "type": "state_transition",
                    "transition": trans.to_dict(),
                    "skeleton": (
                        f"def test_transition_{trans.name}():\n"
                        f"    # Transition: {trans.description}\n"
                        f"    # Precondition: {trans.precondition.description}\n"
                        f"    # Postcondition: {trans.postcondition.description}\n"
                        f"    obj = create_instance()\n"
                        f"    # Setup precondition state\n"
                        f"    obj.{trans.name}()\n"
                        f"    # Verify postcondition\n"
                        f"    assert ...  # TODO"
                    ),
                    "target_function": spec.target_key,
                }
            )

        # Invariant-checking wrapper skeletons
        for inv in spec.invariants:
            targets.scenario_tests.append(
                {
                    "name": f"test_invariant_{inv.name}",
                    "type": "state_invariant",
                    "invariant": inv.to_dict(),
                    "skeleton": (
                        f"def test_invariant_{inv.name}():\n"
                        f"    # Invariant must hold across all transitions: {inv.description}\n"
                        f"    obj = create_instance()\n"
                        f"    for transition in [{', '.join(repr(t.name) for t in spec.allowed_transitions)}]:\n"
                        f"        getattr(obj, transition)()\n"
                        f"        assert ...  # Verify: {inv.description}"
                    ),
                    "target_function": spec.target_key,
                }
            )

        # State variable initialization tests
        for sv in spec.state_variables:
            targets.scenario_tests.append(
                {
                    "name": f"test_init_{sv.name}",
                    "type": "state_init",
                    "state_variable": sv.to_dict(),
                    "skeleton": (
                        f"def test_init_{sv.name}():\n"
                        f"    obj = create_instance()\n"
                        f"    assert obj.{sv.name} == {sv.initial_value}  # {sv.description}"
                    ),
                    "target_function": spec.target_key,
                }
            )

        # Refinement obligations
        for ro in spec.refinement_obligations:
            targets.expected_kill_set[ro.category] = ro.expected_kill

        # Generation constraints
        targets.generation_constraints = [gc.to_dict() for gc in spec.generation_constraints]

        # Compass gate assertions
        for inv in spec.invariants:
            targets.compass_gate_assertions.append(
                {
                    "invariant": inv.name,
                    "description": inv.description,
                    "kind": inv.kind,
                }
            )

        return targets


# ── Distributed Backend ───────────────────────────────────────────────


class DistributedBackend:
    """Communicating state machines (protocol conformance)."""

    def compile(self, spec: PrescriptiveSpec) -> CompilationTargets:
        targets = CompilationTargets()

        # Message sequence tests from transitions
        for trans in spec.allowed_transitions:
            targets.scenario_tests.append(
                {
                    "name": f"test_protocol_{trans.name}",
                    "type": "protocol_conformance",
                    "transition": trans.to_dict(),
                    "skeleton": (
                        f"def test_protocol_{trans.name}():\n"
                        f"    # Protocol step: {trans.description}\n"
                        f"    # Precondition: {trans.precondition.description}\n"
                        f"    # Postcondition: {trans.postcondition.description}\n"
                        f"    # Verify message sequence and state agreement"
                    ),
                    "target_function": spec.target_key,
                }
            )

        # Protocol monitor obligations from invariants
        for inv in spec.invariants:
            targets.scenario_tests.append(
                {
                    "name": f"test_monitor_{inv.name}",
                    "type": "protocol_monitor",
                    "invariant": inv.to_dict(),
                    "skeleton": (
                        f"def test_monitor_{inv.name}():\n"
                        f"    # Monitor: {inv.description}\n"
                        f"    # Verify invariant holds across all protocol states"
                    ),
                    "target_function": spec.target_key,
                }
            )

        # Refinement obligations
        for ro in spec.refinement_obligations:
            targets.expected_kill_set[ro.category] = ro.expected_kill

        # Generation constraints
        targets.generation_constraints = [gc.to_dict() for gc in spec.generation_constraints]

        return targets


# ── Backend selection ─────────────────────────────────────────────────


def select_backend(spec: PrescriptiveSpec) -> PureBackend | StatefulBackend | DistributedBackend:
    """Route based on spec.problem_class."""
    if spec.problem_class == "stateful":
        return StatefulBackend()
    if spec.problem_class == "distributed":
        return DistributedBackend()
    return PureBackend()


# ── Helpers ───────────────────────────────────────────────────────


def _func_call(spec: PrescriptiveSpec) -> str:
    """Generate a function call string from spec interface."""
    func_name = spec.target_key.split("::")[-1] if "::" in spec.target_key else spec.target_key
    params = ", ".join(p.get("name", "arg") for p in spec.parameters)
    return f"{func_name}({params})"


def _build_signature(spec: PrescriptiveSpec) -> str:
    """Build typed function signature from spec parameters + return type."""
    func_name = spec.target_key.split("::")[-1] if "::" in spec.target_key else spec.target_key
    params = []
    for p in spec.parameters:
        name = p.get("name", "arg")
        ptype = p.get("type", "")
        if ptype:
            params.append(f"{name}: {ptype}")
        else:
            params.append(name)
    param_str = ", ".join(params)
    ret = f" -> {spec.return_type}" if spec.return_type else ""
    docstring = _build_docstring(spec)
    body = "    pass  # TODO: implement"
    return f"def {func_name}({param_str}){ret}:\n{docstring}\n{body}"


def _build_docstring(spec: PrescriptiveSpec) -> str:
    """Build docstring from invariant descriptions + return_description."""
    lines = []
    if spec.return_description:
        lines.append(spec.return_description)
    for inv in spec.invariants[:5]:
        lines.append(f"Invariant: {inv.description}")
    if not lines:
        lines.append(f"Implementation for {spec.target_key}.")
    content = "\n    ".join(lines)
    return f'    """{content}"""'

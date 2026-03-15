"""Problem-class backend compilers for PrescriptiveSpec.

Three backends compile PrescriptiveSpec into test skeletons + generation constraints:
- PureBackend: algebraic contract compiler for pure/local functions
- StatefulBackend: state-machine skeleton compiler
- DistributedBackend: communicating state machines (protocol conformance)

Plus PrescriptiveAdapter: bridges CompilationTargets into existing mutation/spec APIs.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .prescriptive_spec import PrescriptiveSpec


# ── CompilationTargets ────────────────────────────────────────────────


@dataclass
class CompilationTargets:
    """Output of backend compilation."""

    property_tests: list[dict[str, Any]] = field(default_factory=list)
    scenario_tests: list[dict[str, Any]] = field(default_factory=list)
    expected_kill_set: dict[str, bool] = field(default_factory=dict)
    compass_gate_assertions: list[dict[str, Any]] = field(default_factory=list)
    generation_constraints: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "property_tests": self.property_tests,
            "scenario_tests": self.scenario_tests,
            "expected_kill_set": self.expected_kill_set,
            "compass_gate_assertions": self.compass_gate_assertions,
            "generation_constraints": self.generation_constraints,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompilationTargets:
        return cls(
            property_tests=data.get("property_tests", []),
            scenario_tests=data.get("scenario_tests", []),
            expected_kill_set=data.get("expected_kill_set", {}),
            compass_gate_assertions=data.get("compass_gate_assertions", []),
            generation_constraints=data.get("generation_constraints", []),
        )


# ── Pure Backend ──────────────────────────────────────────────────────


class PureBackend:
    """Algebraic contract compiler for pure/local functions."""

    def compile(self, spec: PrescriptiveSpec) -> CompilationTargets:
        targets = CompilationTargets()

        # Algebraic laws → Hypothesis property skeletons
        for law in spec.algebraic_laws:
            name = law.get("name", law.get("property_name", "unnamed"))
            targets.property_tests.append({
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
            })

        # Invariants → exact-value test skeletons
        for inv in spec.invariants:
            targets.scenario_tests.append({
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
            })

        # Forbidden behaviors → negative test skeletons
        for i, fb in enumerate(spec.forbidden_behaviors):
            targets.scenario_tests.append({
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
            })

        # Refinement obligations → expected mutation kill set
        for ro in spec.refinement_obligations:
            targets.expected_kill_set[ro.category] = ro.expected_kill

        # Generation constraints
        targets.generation_constraints = [gc.to_dict() for gc in spec.generation_constraints]

        # Compass gate assertions from invariants
        for inv in spec.invariants:
            targets.compass_gate_assertions.append({
                "invariant": inv.name,
                "description": inv.description,
                "kind": inv.kind,
            })

        return targets


# ── Stateful Backend ──────────────────────────────────────────────────


class StatefulBackend:
    """State-machine skeleton compiler."""

    def compile(self, spec: PrescriptiveSpec) -> CompilationTargets:
        targets = CompilationTargets()

        # One test per allowed transition verifying postconditions
        for trans in spec.allowed_transitions:
            targets.scenario_tests.append({
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
            })

        # Invariant-checking wrapper skeletons
        for inv in spec.invariants:
            targets.scenario_tests.append({
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
            })

        # State variable initialization tests
        for sv in spec.state_variables:
            targets.scenario_tests.append({
                "name": f"test_init_{sv.name}",
                "type": "state_init",
                "state_variable": sv.to_dict(),
                "skeleton": (
                    f"def test_init_{sv.name}():\n"
                    f"    obj = create_instance()\n"
                    f"    assert obj.{sv.name} == {sv.initial_value}  # {sv.description}"
                ),
                "target_function": spec.target_key,
            })

        # Refinement obligations
        for ro in spec.refinement_obligations:
            targets.expected_kill_set[ro.category] = ro.expected_kill

        # Generation constraints
        targets.generation_constraints = [gc.to_dict() for gc in spec.generation_constraints]

        # Compass gate assertions
        for inv in spec.invariants:
            targets.compass_gate_assertions.append({
                "invariant": inv.name,
                "description": inv.description,
                "kind": inv.kind,
            })

        return targets


# ── Distributed Backend ───────────────────────────────────────────────


class DistributedBackend:
    """Communicating state machines (protocol conformance)."""

    def compile(self, spec: PrescriptiveSpec) -> CompilationTargets:
        targets = CompilationTargets()

        # Message sequence tests from transitions
        for trans in spec.allowed_transitions:
            targets.scenario_tests.append({
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
            })

        # Protocol monitor obligations from invariants
        for inv in spec.invariants:
            targets.scenario_tests.append({
                "name": f"test_monitor_{inv.name}",
                "type": "protocol_monitor",
                "invariant": inv.to_dict(),
                "skeleton": (
                    f"def test_monitor_{inv.name}():\n"
                    f"    # Monitor: {inv.description}\n"
                    f"    # Verify invariant holds across all protocol states"
                ),
                "target_function": spec.target_key,
            })

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


# ── Adapter ───────────────────────────────────────────────────────────


class PrescriptiveAdapter:
    """Bridges CompilationTargets into existing mutation/spec tool APIs."""

    def materialize_test_file(
        self,
        targets: CompilationTargets,
        spec: PrescriptiveSpec,
        output_path: str,
    ) -> str:
        """Write property_tests + scenario_tests as a real pytest file.

        Test file naming follows discover_test_files patterns.
        """
        has_property_tests = bool(targets.property_tests)
        lines = [
            '"""Auto-generated prescriptive spec tests.',
            f"Target: {spec.target_key}",
            f"Problem class: {spec.problem_class}",
            f'Generated at: {time.strftime("%Y-%m-%d %H:%M:%S")}',
            '"""',
            "",
            "import pytest",
        ]
        if has_property_tests:
            lines.append("from hypothesis import given, strategies as st")
        lines.append("")

        # Emit property tests
        for pt in targets.property_tests:
            lines.append("")
            lines.append(pt.get("skeleton", f"# {pt['name']}"))
            lines.append("")

        # Emit scenario tests
        for st in targets.scenario_tests:
            lines.append("")
            lines.append(st.get("skeleton", f"# {st['name']}"))
            lines.append("")

        content = "\n".join(lines) + "\n"
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return output_path

    def persist_kill_expectations(
        self,
        spec: PrescriptiveSpec,
        targets: CompilationTargets,
        project_root: str,
    ) -> None:
        """Write expected_kill_set alongside the spec."""
        from .prescriptive_spec import _SPEC_DIR, _target_hash

        spec_dir = os.path.join(project_root, _SPEC_DIR)
        os.makedirs(spec_dir, exist_ok=True)

        h = _target_hash(spec.target_key)
        expectations_path = os.path.join(spec_dir, f"{h}_expectations.json")
        with open(expectations_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "spec_id": spec.spec_id,
                    "target_key": spec.target_key,
                    "expected_kill_set": targets.expected_kill_set,
                    "timestamp": time.time(),
                },
                f,
                indent=2,
            )

    def verify_refinement(
        self,
        spec: PrescriptiveSpec,
        targets: CompilationTargets,
        project_root: str,
        file: str,
        function: str | None = None,
    ) -> dict[str, Any]:
        """Run refinement check with two evidence classes.

        Structural evidence: AST-level invariant checks (fast, deterministic).
        Behavioral evidence: mutation kill expectations vs cached state.
        Convergence: prescriptive vs retrospective sigma ratio.

        Returns verdict with per-class results so consumers know
        what kind of evidence supports each obligation status.
        """
        from .prescriptive_sigma import compute_convergence_signal

        verdict: dict[str, Any] = {
            "spec_id": spec.spec_id,
            "target_key": spec.target_key,
            "structural_evidence": [],
            "behavioral_evidence": [],
            "convergence": None,
            "overall": "unknown",
        }

        # ── Structural evidence (AST checker) ─────────────────────
        structural_pass = 0
        structural_fail = 0
        structural_skip = 0

        func_name = function
        if not func_name and "::" in spec.target_key:
            func_name = spec.target_key.split("::")[-1]

        if func_name and spec.invariants:
            source = self._read_source(project_root, file)
            if source:
                from .prescriptive_ast_checker import check_invariants_against_ast

                results = check_invariants_against_ast(source, func_name, spec.invariants)
                for r in results:
                    verdict["structural_evidence"].append(r.to_dict())
                    if r.status == "pass":
                        structural_pass += 1
                    elif r.status == "fail":
                        structural_fail += 1
                    else:
                        structural_skip += 1

        # ── Convergence ───────────────────────────────────────────
        retro_sigma = self._load_retrospective_sigma(project_root, spec.target_key)
        if retro_sigma is not None:
            verdict["convergence"] = compute_convergence_signal(
                spec.prescriptive_sigma, retro_sigma
            )

        # ── Behavioral evidence (mutation state) ──────────────────
        mutation_state = self._load_mutation_state(project_root, spec.target_key)
        behavioral_pass = 0
        behavioral_fail = 0
        behavioral_unknown = 0

        for cat, expected_kill in targets.expected_kill_set.items():
            ob: dict[str, Any] = {
                "category": cat,
                "expected_kill": expected_kill,
                "status": "unknown",
            }

            if mutation_state:
                actual = self._check_category_killed(mutation_state, cat)
                if actual is not None:
                    if actual == expected_kill:
                        ob["status"] = "pass"
                        behavioral_pass += 1
                    else:
                        ob["status"] = "fail"
                        behavioral_fail += 1
                else:
                    behavioral_unknown += 1
            else:
                behavioral_unknown += 1

            verdict["behavioral_evidence"].append(ob)

        # ── Overall verdict (both evidence classes) ───────────────
        total_fail = structural_fail + behavioral_fail
        total_pass = structural_pass + behavioral_pass
        total_unknown = structural_skip + behavioral_unknown

        if total_fail > 0:
            verdict["overall"] = "fail"
        elif total_unknown > 0 and total_pass > 0:
            verdict["overall"] = "partial"
        elif total_unknown > 0:
            verdict["overall"] = "unknown"
        elif total_pass > 0:
            verdict["overall"] = "pass"

        verdict["summary"] = {
            "structural": {"pass": structural_pass, "fail": structural_fail, "skip": structural_skip},
            "behavioral": {"pass": behavioral_pass, "fail": behavioral_fail, "unknown": behavioral_unknown},
        }

        return verdict

    def _read_source(self, project_root: str, file: str) -> str | None:
        """Read source file for AST checking."""
        full_path = file if os.path.isabs(file) else os.path.join(project_root, file)
        if not os.path.isfile(full_path):
            return None
        try:
            with open(full_path, encoding="utf-8") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return None

    def _load_retrospective_sigma(self, project_root: str, target_key: str) -> int | None:
        """Load retrospective sigma from spec cache."""
        cache_dir = os.path.join(project_root, ".lintgate", "spec_cache")
        if not os.path.isdir(cache_dir):
            return None

        # Try to find the function in any cached ledger
        for fname in os.listdir(cache_dir):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(cache_dir, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    data = json.load(f)
                funcs = data.get("functions", {})
                if target_key in funcs:
                    return funcs[target_key].get("estimated_sigma", 0)
            except (OSError, ValueError, KeyError):
                continue
        return None

    def _load_mutation_state(self, project_root: str, target_key: str) -> dict[str, Any] | None:
        """Load mutation cache for a function."""
        cache_dir = os.path.join(project_root, ".lintgate", "mutation")
        if not os.path.isdir(cache_dir):
            return None

        for fname in os.listdir(cache_dir):
            if fname == "scheduler_state.json" or not fname.endswith(".json"):
                continue
            fpath = os.path.join(cache_dir, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("function_key") == target_key:
                    return data
            except (OSError, ValueError):
                continue
        return None

    def _check_category_killed(self, mutation_state: dict[str, Any], category: str) -> bool | None:
        """Check if a mutation category was killed in the mutation state."""
        for cat_data in mutation_state.get("per_category", []):
            if cat_data.get("category") == category:
                survived = cat_data.get("survived", 0)
                return survived == 0
        return None


# ── Helpers ───────────────────────────────────────────────────────────


def _func_call(spec: PrescriptiveSpec) -> str:
    """Generate a function call string from spec interface."""
    func_name = spec.target_key.split("::")[-1] if "::" in spec.target_key else spec.target_key
    params = ", ".join(p.get("name", "arg") for p in spec.parameters)
    return f"{func_name}({params})"

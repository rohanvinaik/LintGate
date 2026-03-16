"""Synthesis gate, witness records, and stub helpers for prescriptive compilation."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .backends import CompilationTargets
    from .spec import PrescriptiveSpec


def _classify_return_type(return_type: str) -> str:
    """Classify return type into scalar/simple_container/complex/unknown."""
    if not return_type:
        return "unknown"
    rt = return_type.strip()
    # Scalars
    if rt in ("int", "float", "bool", "str", "bytes", "None"):
        return "scalar"
    # Simple containers: dict[K, V], list[T], set[T], tuple[T, ...]
    lower = rt.lower()
    for prefix in ("dict[", "list[", "set[", "tuple["):
        if lower.startswith(prefix):
            # Check nesting depth — only one level of generics is "simple"
            inner = rt[len(prefix) : -1] if rt.endswith("]") else ""
            # If inner itself contains brackets, it's complex
            if "[" in inner:
                return "complex"
            return "simple_container"
    # Optional[T] → classify T
    if lower.startswith("optional[") and rt.endswith("]"):
        inner = rt[9:-1]
        return _classify_return_type(inner)
    # Union, Callable, etc. → complex
    if "[" in rt:
        return "complex"
    # Bare names (custom types) → unknown
    return "unknown"


def _build_synthesis_profile(spec: PrescriptiveSpec) -> dict[str, Any]:
    """Build gate eligibility metadata for the synthesis gate."""
    return_type_cat = _classify_return_type(spec.return_type)

    # Check for CUSTOM-only invariants
    has_only_custom = _has_only_custom_predicates(spec)

    # Check for CALLS predicates or side effects
    from .spec import PredicateOp

    has_calls = any(inv.predicate.op == PredicateOp.CALLS for inv in spec.invariants)

    gate_reasons: list[str] = []
    gate_eligible = True

    if spec.problem_class != "pure":
        gate_eligible = False
        gate_reasons.append(f"problem_class={spec.problem_class}, not pure")
    if len(spec.parameters) > 3:
        gate_eligible = False
        gate_reasons.append(f"param_count={len(spec.parameters)} > 3")
    if return_type_cat not in ("scalar", "simple_container"):
        gate_eligible = False
        gate_reasons.append(f"return_type_category={return_type_cat}")
    if has_only_custom:
        gate_eligible = False
        gate_reasons.append("all invariants are CUSTOM with no structural predicates")
    if spec.allowed_side_effects:
        gate_eligible = False
        gate_reasons.append("has allowed_side_effects")
    if spec.state_variables:
        gate_eligible = False
        gate_reasons.append("has state_variables")
    if has_calls:
        gate_eligible = False
        gate_reasons.append("has CALLS predicates")

    return {
        "problem_class": spec.problem_class,
        "param_count": len(spec.parameters),
        "return_type": spec.return_type,
        "return_type_category": return_type_cat,
        "has_custom_predicates": any(
            inv.predicate.op == PredicateOp.CUSTOM for inv in spec.invariants
        ),
        "has_executable_witnesses": False,  # populated by generate_executable_witnesses
        "gate_eligible": gate_eligible,
        "gate_reasons": gate_reasons,
    }


def _has_only_custom_predicates(spec: PrescriptiveSpec) -> bool:
    """Return True if ALL invariants are CUSTOM with no structural predicates."""
    from .spec import PredicateOp

    if not spec.invariants:
        return False
    return all(inv.predicate.op == PredicateOp.CUSTOM for inv in spec.invariants)


# ── Synthesis Gate ───────────────────────────────────────────────────


@dataclass
class SynthesisGateResult:
    """Result of synthesis gate eligibility check."""

    eligible: bool
    reasons: list[str]
    profile: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reasons": self.reasons,
            "profile": self.profile,
        }


def check_synthesis_gate(
    spec: PrescriptiveSpec, targets: CompilationTargets
) -> SynthesisGateResult:
    """Strict, fast check before attempting zero-token synthesis.

    ALL conditions must pass:
    1. problem_class == "pure"
    2. len(spec.parameters) <= 3
    3. return_type_category in ("scalar", "simple_container")
    4. At least one non-CUSTOM typed predicate exists
    5. Executable witnesses exist (has_oracle_value: True)
    6. No allowed_side_effects, no state_variables, no CALLS predicates
    """
    profile = targets.synthesis_profile
    if not profile:
        profile = _build_synthesis_profile(spec)

    reasons: list[str] = list(profile.get("gate_reasons", []))

    # Condition 5: executable witnesses
    has_witnesses = profile.get("has_executable_witnesses", False)
    if not has_witnesses:
        reasons.append("no executable witnesses with oracle values")

    eligible = profile.get("gate_eligible", False) and has_witnesses

    return SynthesisGateResult(
        eligible=eligible,
        reasons=reasons if not eligible else [],
        profile=profile,
    )


# ── Witness Records ──────────────────────────────────────────────────


@dataclass
class WitnessRecord:
    """Input→output pair with oracle values for synthesis validation."""

    inputs: dict[str, str]  # param_name → Python expression string
    output: str | None  # Python expression of actual output, or None if no oracle
    has_oracle_value: bool  # True only if output was captured from real execution
    imports: list[str] = field(default_factory=list)  # required import lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "inputs": self.inputs,
            "output": self.output,
            "has_oracle_value": self.has_oracle_value,
            "imports": self.imports,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WitnessRecord:
        return cls(
            inputs=data.get("inputs", {}),
            output=data.get("output"),
            has_oracle_value=data.get("has_oracle_value", False),
            imports=data.get("imports", []),
        )


def generate_executable_witnesses(
    spec: PrescriptiveSpec,
    project_root: str,
) -> list[WitnessRecord]:
    """Generate input→output witness pairs for synthesis validation.

    Uses typed_synthesis for input generation, then attempts oracle execution
    if the function already exists on disk (retrospective mode).
    """
    from lintgate.testing.typed_synthesis import synthesize_value

    witnesses: list[WitnessRecord] = []

    # Generate concrete inputs for each parameter
    inputs: dict[str, str] = {}
    all_imports: list[str] = []
    for p in spec.parameters:
        ptype = p.get("type", "")
        pname = p.get("name", "arg")
        sv = synthesize_value(ptype, pname)
        inputs[pname] = sv.code
        all_imports.extend(sv.imports)

    # Deduplicate imports
    all_imports = list(dict.fromkeys(all_imports))

    # Try oracle execution if function exists on disk
    output: str | None = None
    has_oracle = False

    if spec.mode == "retrospective" and "::" in spec.target_key:
        module_path, func_name = spec.target_key.rsplit("::", 1)
        module_dotted = module_path.replace("/", ".")
        file_path = os.path.join(project_root, module_path.replace(".", "/") + ".py")

        if os.path.isfile(file_path):
            # Build call expression
            args_str = ", ".join(f"{k}={v}" for k, v in inputs.items())
            import_lines = "\n".join(all_imports) if all_imports else ""
            script = (
                f"import sys; sys.path.insert(0, {project_root!r})\n"
                f"{import_lines}\n"
                f"from {module_dotted} import {func_name}\n"
                f"result = {func_name}({args_str})\n"
                f"print(repr(result))"
            )
            try:
                proc = subprocess.run(
                    [sys.executable, "-c", script],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    cwd=project_root,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    output = proc.stdout.strip()
                    has_oracle = True
            except (subprocess.TimeoutExpired, OSError):
                pass

    witnesses.append(
        WitnessRecord(
            inputs=inputs,
            output=output,
            has_oracle_value=has_oracle,
            imports=all_imports,
        )
    )
    return witnesses

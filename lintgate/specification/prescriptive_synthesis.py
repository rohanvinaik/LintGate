"""DSL body synthesizer for narrow class of fully-determined pure functions.

v1 patterns (spec IR fully determines the implementation):
- key_inversion: dict[K, list[V]] → dict[V, list[K]]
- count_aggregation: list → dict[K, int]
- group_aggregation: list → dict[K, list[V]]
- field_projection: dict/dataclass → scalar (extract one field)

Each pattern is recognized from type signatures + invariant signals,
then synthesized as a Python function body. Synthesized bodies are
validated with ast.parse() and optionally against executable witnesses.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .prescriptive_backends import WitnessRecord
    from .prescriptive_spec import PrescriptiveSpec


# ── Core Types ───────────────────────────────────────────────────────


@dataclass
class PatternMatch:
    """A recognized synthesis pattern with parameters."""

    pattern_name: str
    confidence: float
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class SynthesisResult:
    """Result of body synthesis attempt."""

    success: bool
    body: str = ""
    pattern_used: str = ""
    confidence: float = 0.0
    failure_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "body": self.body,
            "pattern_used": self.pattern_used,
            "confidence": self.confidence,
            "failure_reason": self.failure_reason,
        }


# ── Pattern Recognizers ──────────────────────────────────────────────


def _recognize_key_inversion(spec: PrescriptiveSpec) -> PatternMatch | None:
    """dict[K, list[V]] → dict[V, list[K]] — only one correct implementation."""
    rt = spec.return_type.strip()
    if len(spec.parameters) != 1:
        return None

    param = spec.parameters[0]
    ptype = param.get("type", "").strip()

    # Match dict[K, list[V]] input
    input_match = _parse_dict_of_list(ptype)
    if not input_match:
        return None
    input_k, input_v = input_match

    # Match dict[V, list[K]] output (swapped)
    output_match = _parse_dict_of_list(rt)
    if not output_match:
        return None
    output_k, output_v = output_match

    # Key/value types must be swapped
    if input_k == output_k and input_v == output_v:
        # Same types — not an inversion, might be identity
        return None
    if input_k != output_v or input_v != output_k:
        # Types don't match the inversion pattern
        return None

    return PatternMatch(
        pattern_name="key_inversion",
        confidence=0.95,
        params={
            "input_param": param.get("name", "data"),
            "input_key_type": input_k,
            "input_value_type": input_v,
        },
    )


def _recognize_count_aggregation(spec: PrescriptiveSpec) -> PatternMatch | None:
    """list → dict[K, int] — count occurrences by key."""
    rt = spec.return_type.strip()
    if len(spec.parameters) != 1:
        return None

    param = spec.parameters[0]
    ptype = param.get("type", "").strip().lower()

    # Input must be a list
    if not ptype.startswith("list["):
        return None

    # Output must be dict[K, int]
    out_match = _parse_dict_type(rt)
    if not out_match:
        return None
    _, out_v = out_match
    if out_v.strip().lower() != "int":
        return None

    # Look for count/frequency signals in invariants
    has_count_signal = _has_invariant_signal(spec, {"count", "frequency", "occurrences", "tally"})
    if not has_count_signal:
        return None

    return PatternMatch(
        pattern_name="count_aggregation",
        confidence=0.85,
        params={
            "input_param": param.get("name", "items"),
        },
    )


def _recognize_group_aggregation(spec: PrescriptiveSpec) -> PatternMatch | None:
    """list → dict[K, list[V]] — group by key."""
    rt = spec.return_type.strip()
    if len(spec.parameters) != 1:
        return None

    param = spec.parameters[0]
    ptype = param.get("type", "").strip().lower()

    # Input must be a list
    if not ptype.startswith("list["):
        return None

    # Output must be dict[K, list[V]]
    out_match = _parse_dict_of_list(rt)
    if not out_match:
        return None

    # Look for group/collect signals in invariants
    has_group_signal = _has_invariant_signal(spec, {"group", "collect", "bucket", "partition", "categorize"})
    if not has_group_signal:
        return None

    # Need a key_func hint — look for field/attribute/key mentions
    key_attr = _extract_key_attribute(spec)

    return PatternMatch(
        pattern_name="group_aggregation",
        confidence=0.80,
        params={
            "input_param": param.get("name", "items"),
            "key_attr": key_attr,
        },
    )


def _recognize_field_projection(spec: PrescriptiveSpec) -> PatternMatch | None:
    """dict/dataclass → scalar — extract one field with .get() default."""
    rt = spec.return_type.strip()
    if len(spec.parameters) != 1:
        return None

    # Return must be scalar
    if rt.lower() not in ("int", "float", "bool", "str", "bytes", "none"):
        return None

    param = spec.parameters[0]
    ptype = param.get("type", "").strip().lower()

    # Input must be dict-like
    is_dict = ptype.startswith("dict[") or ptype == "dict"
    if not is_dict:
        return None

    # Must have exactly one HAS_ATTR invariant or field-name signal
    from .prescriptive_spec import PredicateOp

    field_name = None
    has_attr_count = 0
    for inv in spec.invariants:
        if inv.predicate.op == PredicateOp.HAS_ATTR:
            field_name = inv.predicate.value or inv.predicate.subject
            has_attr_count += 1
        elif inv.predicate.op == PredicateOp.CUSTOM:
            # Try to extract field name from description
            extracted = _extract_field_from_description(inv.description)
            if extracted:
                field_name = extracted
                has_attr_count += 1

    if has_attr_count != 1 or not field_name:
        return None

    # Determine default value based on return type
    defaults = {"int": "0", "float": "0.0", "bool": "False", "str": '""', "bytes": 'b""', "none": "None"}
    default_val = defaults.get(rt.lower(), "None")

    return PatternMatch(
        pattern_name="field_projection",
        confidence=0.90,
        params={
            "input_param": param.get("name", "data"),
            "field_name": field_name,
            "default_value": default_val,
        },
    )


# ── Body Generators ──────────────────────────────────────────────────


def _generate_key_inversion(match: PatternMatch) -> str:
    """Generate body for dict key inversion."""
    param = match.params["input_param"]
    return (
        f"    result: dict = {{}}\n"
        f"    for key, values in {param}.items():\n"
        f"        for val in values:\n"
        f"            result.setdefault(val, []).append(key)\n"
        f"    return result"
    )


def _generate_count_aggregation(match: PatternMatch) -> str:
    """Generate body for counting occurrences."""
    param = match.params["input_param"]
    return (
        f"    counts: dict = {{}}\n"
        f"    for item in {param}:\n"
        f"        counts[item] = counts.get(item, 0) + 1\n"
        f"    return counts"
    )


def _generate_group_aggregation(match: PatternMatch) -> str:
    """Generate body for grouping by key."""
    param = match.params["input_param"]
    key_attr = match.params.get("key_attr", "")
    if key_attr:
        key_expr = f"item.{key_attr}" if "." not in key_attr else f"item[{key_attr!r}]"
        return (
            f"    groups: dict = {{}}\n"
            f"    for item in {param}:\n"
            f"        key = {key_expr}\n"
            f"        groups.setdefault(key, []).append(item)\n"
            f"    return groups"
        )
    # Fallback: group by identity (less useful but valid)
    return (
        f"    groups: dict = {{}}\n"
        f"    for item in {param}:\n"
        f"        groups.setdefault(item, []).append(item)\n"
        f"    return groups"
    )


def _generate_field_projection(match: PatternMatch) -> str:
    """Generate body for extracting a single field."""
    param = match.params["input_param"]
    field_name = match.params["field_name"]
    default = match.params["default_value"]
    return f"    return {param}.get({field_name!r}, {default})"


# ── Pattern Registry ─────────────────────────────────────────────────

_RECOGNIZERS: list[tuple[str, Any, Any]] = [
    ("key_inversion", _recognize_key_inversion, _generate_key_inversion),
    ("field_projection", _recognize_field_projection, _generate_field_projection),
    ("count_aggregation", _recognize_count_aggregation, _generate_count_aggregation),
    ("group_aggregation", _recognize_group_aggregation, _generate_group_aggregation),
]


# ── Orchestrator ─────────────────────────────────────────────────────


def synthesize_body(
    spec: PrescriptiveSpec,
    witnesses: list[WitnessRecord] | None = None,
    project_root: str = "",
) -> SynthesisResult:
    """Try patterns in specificity order, return first match.

    Validates with ast.parse(). If executable witnesses exist,
    runs the synthesized function against them to verify correctness.
    """
    for _, recognizer, generator in _RECOGNIZERS:
        match = recognizer(spec)
        if match is None:
            continue

        body = generator(match)

        # Validate syntax
        func_name = spec.target_key.split("::")[-1] if "::" in spec.target_key else spec.target_key
        full_func = f"def {func_name}():\n{body}"
        try:
            ast.parse(full_func)
        except SyntaxError as e:
            return SynthesisResult(
                success=False,
                body=body,
                pattern_used=match.pattern_name,
                failure_reason=f"syntax error in synthesized body: {e}",
            )

        # Validate against witnesses if available
        if witnesses and project_root:
            witness_ok = _validate_against_witnesses(
                spec, body, witnesses, project_root
            )
            if not witness_ok:
                return SynthesisResult(
                    success=False,
                    body=body,
                    pattern_used=match.pattern_name,
                    confidence=match.confidence,
                    failure_reason="synthesized body failed witness validation",
                )

        return SynthesisResult(
            success=True,
            body=body,
            pattern_used=match.pattern_name,
            confidence=match.confidence,
        )

    return SynthesisResult(
        success=False,
        failure_reason="no pattern matched",
    )


# ── Witness Validation ───────────────────────────────────────────────


def _validate_against_witnesses(
    spec: PrescriptiveSpec,
    body: str,
    witnesses: list[WitnessRecord],
    project_root: str,
) -> bool:
    """Run synthesized body against executable witnesses."""
    oracle_witnesses = [w for w in witnesses if w.has_oracle_value and w.output]
    if not oracle_witnesses:
        return True  # No oracle witnesses → can't invalidate

    func_name = spec.target_key.split("::")[-1] if "::" in spec.target_key else spec.target_key

    # Build typed params
    params = []
    for p in spec.parameters:
        name = p.get("name", "arg")
        ptype = p.get("type", "")
        params.append(f"{name}: {ptype}" if ptype else name)
    param_str = ", ".join(params)
    ret = f" -> {spec.return_type}" if spec.return_type else ""

    func_code = f"def {func_name}({param_str}){ret}:\n{body}"

    for witness in oracle_witnesses:
        args_str = ", ".join(f"{k}={v}" for k, v in witness.inputs.items())
        import_lines = "\n".join(witness.imports) if witness.imports else ""
        script = (
            f"{import_lines}\n"
            f"{func_code}\n"
            f"result = {func_name}({args_str})\n"
            f"expected = {witness.output}\n"
            f"assert result == expected, f'got {{result!r}}, expected {{expected!r}}'"
        )
        try:
            proc = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=project_root,
            )
            if proc.returncode != 0:
                return False
        except (subprocess.TimeoutExpired, OSError):
            return False

    return True


# ── Type Parsing Helpers ─────────────────────────────────────────────


def _parse_dict_type(type_str: str) -> tuple[str, str] | None:
    """Parse dict[K, V] → (K, V) or None."""
    s = type_str.strip()
    if not s.lower().startswith("dict[") or not s.endswith("]"):
        return None
    inner = s[5:-1].strip()
    # Split on top-level comma (respect nested brackets)
    parts = _split_top_level(inner)
    if len(parts) != 2:
        return None
    return parts[0].strip(), parts[1].strip()


def _parse_dict_of_list(type_str: str) -> tuple[str, str] | None:
    """Parse dict[K, list[V]] → (K, V) or None."""
    outer = _parse_dict_type(type_str)
    if not outer:
        return None
    k, v_str = outer
    v_lower = v_str.strip().lower()
    if not v_lower.startswith("list[") or not v_str.strip().endswith("]"):
        return None
    inner_v = v_str.strip()[5:-1].strip()
    return k, inner_v


def _split_top_level(s: str) -> list[str]:
    """Split string on top-level commas, respecting brackets."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in s:
        if ch in ("[", "("):
            depth += 1
            current.append(ch)
        elif ch in ("]", ")"):
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


# ── Invariant Signal Helpers ─────────────────────────────────────────


def _has_invariant_signal(spec: PrescriptiveSpec, keywords: set[str]) -> bool:
    """Check if any invariant description contains one of the keywords."""
    for inv in spec.invariants:
        desc_lower = inv.description.lower()
        if any(kw in desc_lower for kw in keywords):
            return True
    return False


def _extract_key_attribute(spec: PrescriptiveSpec) -> str:
    """Try to extract key attribute name from invariant descriptions."""
    from .prescriptive_spec import PredicateOp

    for inv in spec.invariants:
        if inv.predicate.op == PredicateOp.HAS_ATTR:
            return str(inv.predicate.value or inv.predicate.subject)
        # Try regex on description: "group by X", "keyed by X", "bucket on X"
        desc = inv.description.lower()
        for pattern in [r"group\s+by\s+(\w+)", r"keyed?\s+by\s+(\w+)", r"bucket\s+on\s+(\w+)"]:
            m = re.search(pattern, desc)
            if m:
                return m.group(1)
    return ""


def _extract_field_from_description(description: str) -> str | None:
    """Try to extract a field name from an invariant description."""
    desc = description.lower()
    # Patterns: "extract X", "get X", "field X", "key X"
    for pattern in [
        r"extract\s+['\"]?(\w+)['\"]?",
        r"get\s+['\"]?(\w+)['\"]?",
        r"field\s+['\"]?(\w+)['\"]?",
        r"key\s+['\"]?(\w+)['\"]?",
    ]:
        m = re.search(pattern, desc)
        if m:
            return m.group(1)
    return None

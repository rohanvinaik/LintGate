"""Witness generation — concrete test suggestions from survivor records.

Converts surviving mutant records into actionable prescriptions with
specific inputs, expected behaviors, and assertion shapes. Handles
VALUE, BOUNDARY, SWAP, STATE, and TYPE categories with category-specific
witness templates.
"""

from __future__ import annotations

from typing import Any


def generate_witness_prescription(
    survivor: dict[str, Any],
    func_key: str,
) -> dict[str, Any]:
    """Generate a grounded prescription from a single survivor record.

    Returns a prescription dict with mutant-specific evidence when
    possible, falling back to category-level advice when not.
    """
    category = survivor.get("category", "")
    mutant_id = survivor.get("mutant_id", "")
    diff_summary = survivor.get("diff_summary", "")
    description = survivor.get("description", "")

    generator = _CATEGORY_GENERATORS.get(category, _generic_witness)
    witness = generator(survivor, func_key)

    witness["function"] = func_key
    witness["mutant_id"] = mutant_id
    witness["category"] = category
    witness["source_of_evidence"] = "survivor_record" if diff_summary else "category_template"
    witness["diff_summary"] = diff_summary
    witness["description"] = description

    return witness


def _value_witness(survivor: dict[str, Any], func_key: str) -> dict[str, Any]:
    """Generate witness for VALUE mutations (constant replacement)."""
    short_name = _short_name(func_key)
    diff = survivor.get("diff_summary", "")

    return {
        "why_this_matters": (
            "A VALUE mutation survived: a constant was changed but no test "
            "detected the difference. This means tests don't verify exact output values."
        ),
        "suggested_input": _infer_input_from_diff(diff, "boundary or typical value"),
        "expected_behavior": "Function should return a specific value that changes when the constant changes",
        "assertion_shape": f"assert {short_name}(input) == EXPECTED_VALUE",
        "confidence": 0.8 if diff else 0.5,
        "needs_source_review": not bool(diff),
    }


def _boundary_witness(survivor: dict[str, Any], func_key: str) -> dict[str, Any]:
    """Generate witness for BOUNDARY mutations (comparator flip)."""
    short_name = _short_name(func_key)
    diff = survivor.get("diff_summary", "")

    return {
        "why_this_matters": (
            "A BOUNDARY mutation survived: a comparator was flipped (e.g., < to <=) "
            "but no test caught the off-by-one. Tests don't exercise boundary values."
        ),
        "suggested_input": _infer_input_from_diff(diff, "value at the exact boundary"),
        "expected_behavior": "Behavior should differ at boundary vs boundary±1",
        "assertion_shape": (
            f"assert {short_name}(boundary) != {short_name}(boundary + 1)  "
            f"# or assert {short_name}(boundary) == EXPECTED"
        ),
        "confidence": 0.85 if diff else 0.5,
        "needs_source_review": not bool(diff),
    }


def _swap_witness(survivor: dict[str, Any], func_key: str) -> dict[str, Any]:
    """Generate witness for SWAP mutations (argument transposition)."""
    short_name = _short_name(func_key)

    return {
        "why_this_matters": (
            "A SWAP mutation survived: two arguments were transposed but no test "
            "detected the difference. Tests don't verify parameter ordering matters."
        ),
        "suggested_input": "two distinct values where order matters (e.g., a=1, b=2)",
        "expected_behavior": "f(a, b) should differ from f(b, a) for non-commutative operations",
        "assertion_shape": f"assert {short_name}(a, b) != {short_name}(b, a)",
        "confidence": 0.75,
        "needs_source_review": False,
    }


def _state_witness(survivor: dict[str, Any], func_key: str) -> dict[str, Any]:
    """Generate witness for STATE mutations (assignment removal / return None)."""
    short_name = _short_name(func_key)
    description = survivor.get("description", "")

    if "return_none" in description:
        return {
            "why_this_matters": (
                "A STATE mutation survived: return value was replaced with None "
                "but no test checked the return value."
            ),
            "suggested_input": "typical input that exercises the return path",
            "expected_behavior": "Function should return a non-None value",
            "assertion_shape": f"result = {short_name}(...); assert result is not None",
            "confidence": 0.7,
            "needs_source_review": False,
        }

    return {
        "why_this_matters": (
            "A STATE mutation survived: a self.attr assignment was removed "
            "but no test verified the attribute was set."
        ),
        "suggested_input": "input that triggers the state assignment",
        "expected_behavior": "After calling the method, the attribute should be set",
        "assertion_shape": f"obj.{short_name}(input); assert obj.attr == EXPECTED",
        "confidence": 0.65,
        "needs_source_review": True,
    }


def _type_witness(survivor: dict[str, Any], func_key: str) -> dict[str, Any]:
    """Generate witness for TYPE mutations (isinstance → True)."""
    short_name = _short_name(func_key)

    return {
        "why_this_matters": (
            "A TYPE mutation survived: isinstance() was replaced with True "
            "but no test sent an invalid type to verify the type check matters."
        ),
        "suggested_input": "an input of the wrong type that should be rejected",
        "expected_behavior": "Function should behave differently for wrong types",
        "assertion_shape": f"assert {short_name}(valid_type) != {short_name}(invalid_type)",
        "confidence": 0.7,
        "needs_source_review": False,
    }


def _generic_witness(survivor: dict[str, Any], func_key: str) -> dict[str, Any]:
    """Fallback witness for unknown categories."""
    category = survivor.get("category", "UNKNOWN")
    return {
        "why_this_matters": f"A {category} mutation survived without test detection.",
        "suggested_input": "inspect the function source to determine appropriate input",
        "expected_behavior": "Behavior should change when the mutation is applied",
        "assertion_shape": "assert function(input) == expected",
        "confidence": 0.4,
        "needs_source_review": True,
    }


_CATEGORY_GENERATORS = {
    "VALUE": _value_witness,
    "BOUNDARY": _boundary_witness,
    "SWAP": _swap_witness,
    "STATE": _state_witness,
    "TYPE": _type_witness,
}


# ── Helpers ───────────────────────────────────────────────────────


def _short_name(func_key: str) -> str:
    """Extract short function name from a function key."""
    if "::" in func_key:
        return func_key.split("::")[-1]
    return func_key


def _infer_input_from_diff(diff: str, fallback: str) -> str:
    """Try to infer a useful input suggestion from the diff summary."""
    if not diff or diff == "AST mutation (unparse unavailable)":
        return fallback

    # Look for numeric constants in the diff that hint at boundary values
    import re
    numbers = re.findall(r'\b(\d+(?:\.\d+)?)\b', diff)
    if numbers:
        vals = sorted(set(numbers))[:3]
        return f"values near {', '.join(vals)} (derived from mutated constants)"

    return fallback

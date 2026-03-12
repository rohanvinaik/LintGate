"""Oracle-light executable properties from mutation survivors.

Transforms surviving mutants into executable relational assertions.
SWAP, BOUNDARY, TYPE, and STATE(return_none) are oracle-light — they
produce valid assertions without needing expected output values.
VALUE and STATE(remove_assign) need oracles and are marked accordingly.

Output structure per specification:
    {inputs, setup_code, assertion_code, preconditions, confidence,
     source_lenses, needs_oracle}
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import ast


@dataclass
class ExecutableProperty:
    """Structured executable test property from a mutation survivor."""

    category: str
    inputs: dict[str, Any]
    setup_code: str  # no indent — caller adds it
    assertion_code: str  # no indent, multi-line OK
    preconditions: list[str]
    confidence: float
    source_lenses: list[str]
    needs_oracle: bool
    function_key: str = ""
    mutant_id: str = ""


def generate_executable_property(
    survivor: dict[str, Any],
    func_key: str,
    func_node: ast.FunctionDef | None = None,
    call_site_inputs: list[dict] | None = None,
) -> ExecutableProperty:
    """Generate an executable property from a mutation survivor record."""
    category = survivor.get("category", "")
    gen = _GENERATORS.get(category, _generic_property)
    prop = gen(survivor, func_key, func_node, call_site_inputs)
    prop.function_key = func_key
    prop.mutant_id = survivor.get("mutant_id", "")
    return prop


# ── Helpers ───────────────────────────────────────────────────────


def _func_info(
    func_key: str, func_node: ast.FunctionDef | None,
) -> tuple[str, str, list[str]]:
    """Extract (module_path, func_name, params)."""
    mod, fname = func_key.rsplit("::", 1) if "::" in func_key else ("", func_key)
    params: list[str] = []
    if func_node is not None:
        params = [a.arg for a in func_node.args.args if a.arg not in ("self", "cls")]
    return mod, fname, params


def _import_line(module_path: str, func_name: str) -> str:
    top = func_name.split(".")[0]
    return f"from {module_path} import {top}" if module_path else ""


def _parse_diff_changes(diff: str) -> list[tuple[str, str]]:
    """Extract changed line pairs from diff_summary."""
    if not diff or "\n+ " not in diff:
        return []
    idx = diff.index("\n+ ")
    orig, mutated = diff[2:idx], diff[idx + 3:]
    return [
        (ol.strip(), ml.strip())
        for ol, ml in zip(orig.split("\n"), mutated.split("\n"), strict=False)
        if ol.strip() != ml.strip()
    ]


# ── Category generators ───────────────────────────────────────────


def _swap_property(
    _survivor: dict, func_key: str,
    func_node: ast.FunctionDef | None, call_site_inputs: list[dict] | None,
) -> ExecutableProperty:
    """SWAP: f(a,b) != f(b,a) — sound when witness confirms order matters."""
    mod, fname, params = _func_info(func_key, func_node)
    setup = _import_line(mod, fname)

    if len(params) < 2:
        return ExecutableProperty(
            category="SWAP", inputs={}, setup_code=setup,
            assertion_code=f"# SWAP survived but {fname} has <2 params",
            preconditions=[], confidence=0.3,
            source_lenses=["mutation"], needs_oracle=True,
        )

    a_val, b_val = _distinct_values(call_site_inputs)
    assertion = (
        f"result_ab = {fname}({a_val}, {b_val})\n"
        f"result_ba = {fname}({b_val}, {a_val})\n"
        f'assert result_ab != result_ba, "SWAP: parameter order should matter"'
    )
    return ExecutableProperty(
        category="SWAP",
        inputs={params[0]: a_val, params[1]: b_val},
        setup_code=setup, assertion_code=assertion,
        preconditions=[f"{params[0]} != {params[1]}", "non-commutative"],
        confidence=0.75 if call_site_inputs else 0.6,
        source_lenses=["mutation"] + (["call_sites"] if call_site_inputs else []),
        needs_oracle=False,
    )


def _boundary_property(
    survivor: dict, func_key: str,
    func_node: ast.FunctionDef | None, call_site_inputs: list[dict] | None,
) -> ExecutableProperty:
    """BOUNDARY: behavior differs at actual predicate boundary pair."""
    mod, fname, params = _func_info(func_key, func_node)
    setup = _import_line(mod, fname)
    diff = survivor.get("diff_summary", "")

    info = _extract_boundary_info(diff)
    if info is None:
        return ExecutableProperty(
            category="BOUNDARY", inputs={}, setup_code=setup,
            assertion_code="# BOUNDARY survived but boundary value not extractable",
            preconditions=[], confidence=0.3,
            source_lenses=["mutation"], needs_oracle=True,
        )

    bval = info["boundary_value"]
    bvar = info.get("variable")
    comp = info.get("comparator", "")
    step = 1 if isinstance(bval, int) else 0.1

    # Build calls: vary boundary param, use call-site values for others
    other_vals = _other_param_values(params, bvar, call_site_inputs)
    call_at = _build_call(fname, params, bvar, bval, other_vals)
    call_before = _build_call(fname, params, bvar, bval - step, other_vals)

    has_unknowns = "..." in call_at
    label = f"{bvar}=" if bvar else ""
    assertion = (
        f"result_at = {call_at}\n"
        f"result_before = {call_before}\n"
        f'assert result_at != result_before, '
        f'"BOUNDARY at {label}{bval}: {comp} should discriminate"'
    )
    return ExecutableProperty(
        category="BOUNDARY",
        inputs={bvar or (params[0] if params else "x"): bval},
        setup_code=setup, assertion_code=assertion,
        preconditions=[f"boundary at {label}{bval} ({comp})"],
        confidence=0.85 if not has_unknowns else 0.5,
        source_lenses=["mutation", "diff_analysis"],
        needs_oracle=has_unknowns,
    )


def _type_property(
    survivor: dict, func_key: str,
    func_node: ast.FunctionDef | None, _call_site_inputs: list[dict] | None,
) -> ExecutableProperty:
    """TYPE: wrong type should raise or produce different behavior."""
    mod, fname, _ = _func_info(func_key, func_node)
    imp = _import_line(mod, fname)
    setup = f"{imp}\nimport pytest" if imp else "import pytest"
    diff = survivor.get("diff_summary", "")
    expected_type = _extract_isinstance_type(diff)

    if not expected_type:
        return ExecutableProperty(
            category="TYPE", inputs={}, setup_code=setup,
            assertion_code=(
                f"with pytest.raises((TypeError, ValueError)):\n"
                f"    {fname}(None)  # TODO: use appropriate invalid type"
            ),
            preconditions=["expected type unknown"], confidence=0.4,
            source_lenses=["mutation"], needs_oracle=True,
        )

    invalid = _INVALID_FOR_TYPE.get(expected_type, "None")
    assertion = (
        f"# isinstance checks {expected_type} — wrong type should be rejected\n"
        f"with pytest.raises((TypeError, ValueError)):\n"
        f"    {fname}({invalid})"
    )
    return ExecutableProperty(
        category="TYPE", inputs={"invalid_type": invalid},
        setup_code=setup, assertion_code=assertion,
        preconditions=[f"input must NOT be {expected_type}"],
        confidence=0.7,
        source_lenses=["mutation", "diff_analysis"],
        needs_oracle=False,
    )


def _state_property(
    survivor: dict, func_key: str,
    func_node: ast.FunctionDef | None, call_site_inputs: list[dict] | None,
) -> ExecutableProperty:
    """STATE: return value or attribute must be verified."""
    mod, fname, params = _func_info(func_key, func_node)
    setup = _import_line(mod, fname)
    desc = survivor.get("description", "")
    diff = survivor.get("diff_summary", "")

    if "return_none" in desc:
        call_args = _call_args_from_sites(call_site_inputs) or "..."
        assertion = (
            f"result = {fname}({call_args})\n"
            f'assert result is not None, "STATE: return value should not be None"'
        )
        return ExecutableProperty(
            category="STATE", inputs={}, setup_code=setup,
            assertion_code=assertion,
            preconditions=["function returns a meaningful value"],
            confidence=0.7, source_lenses=["mutation"],
            needs_oracle=False,
        )

    # remove_assign: oracle-dependent
    attr = _extract_self_attr(diff)
    hint = f"self.{attr}" if attr else "attribute"
    assertion = (
        f"# STATE: {hint} assignment removed — verify it's set after call\n"
        f"# obj = ClassName(...)\n"
        f"# obj.{fname}(...)\n"
        f"# assert obj.{attr or 'ATTR'} == EXPECTED  # FILL"
    )
    return ExecutableProperty(
        category="STATE", inputs={}, setup_code=setup,
        assertion_code=assertion,
        preconditions=["construct object", f"verify {hint}"],
        confidence=0.4 if attr else 0.2,
        source_lenses=["mutation"] + (["diff_analysis"] if attr else []),
        needs_oracle=True,
    )


def _value_property(
    _survivor: dict, func_key: str,
    func_node: ast.FunctionDef | None, call_site_inputs: list[dict] | None,
) -> ExecutableProperty:
    """VALUE: exact output needed — oracle-dependent."""
    mod, fname, _ = _func_info(func_key, func_node)
    setup = _import_line(mod, fname)

    call_args = _call_args_from_sites(call_site_inputs) or "..."
    assertion = f"result = {fname}({call_args})\nassert result == ...  # FILL: expected value"
    return ExecutableProperty(
        category="VALUE", inputs={}, setup_code=setup,
        assertion_code=assertion,
        preconditions=["exact expected value must be determined"],
        confidence=0.3,
        source_lenses=["mutation"] + (["call_sites"] if call_site_inputs else []),
        needs_oracle=True,
    )


def _generic_property(
    survivor: dict, _func_key: str,
    _func_node: ast.FunctionDef | None, _call_site_inputs: list[dict] | None,
) -> ExecutableProperty:
    cat = survivor.get("category", "UNKNOWN")
    return ExecutableProperty(
        category=cat, inputs={}, setup_code="",
        assertion_code=f"# {cat} mutation survived — manual investigation needed",
        preconditions=[], confidence=0.2,
        source_lenses=["mutation"], needs_oracle=True,
    )


_GENERATORS = {
    "SWAP": _swap_property,
    "BOUNDARY": _boundary_property,
    "TYPE": _type_property,
    "STATE": _state_property,
    "VALUE": _value_property,
}


# ── Diff extractors ───────────────────────────────────────────────


def _extract_boundary_info(diff: str) -> dict[str, Any] | None:
    """Extract boundary variable, comparator, and value from diff."""
    for orig, _ in _parse_diff_changes(diff):
        m = re.search(r'(\w+)\s*([<>]=?)\s*(\d+(?:\.\d+)?)', orig)
        if m:
            val = m.group(3)
            return {
                "variable": m.group(1),
                "comparator": m.group(2),
                "boundary_value": float(val) if '.' in val else int(val),
            }
    return None


def _extract_isinstance_type(diff: str) -> str | None:
    for orig, _ in _parse_diff_changes(diff):
        m = re.search(r'isinstance\(\s*\w+\s*,\s*(\w+(?:\.\w+)*)', orig)
        if m:
            return m.group(1)
    return None


def _extract_self_attr(diff: str) -> str | None:
    for orig, _ in _parse_diff_changes(diff):
        m = re.search(r'self\.(\w+)\s*=', orig)
        if m:
            return m.group(1)
    return None


# ── Input helpers ─────────────────────────────────────────────────


_INVALID_FOR_TYPE: dict[str, str] = {
    "str": "42", "int": "'not_int'", "float": "'not_float'",
    "bool": "42", "list": "42", "dict": "42", "tuple": "42",
}


def _distinct_values(sites: list[dict] | None) -> tuple[str, str]:
    if sites:
        for s in sites:
            args = s.get("args", [])
            if len(args) >= 2 and str(args[0]) != str(args[1]):
                return str(args[0]), str(args[1])
    return "1", "2"


def _call_args_from_sites(sites: list[dict] | None) -> str:
    if not sites:
        return ""
    for s in sites:
        ctx = s.get("context", "")
        m = re.search(r'\(([^)]+)\)', ctx)
        if m:
            return m.group(1)
    return ""


def _other_param_values(
    params: list[str], boundary_var: str | None, sites: list[dict] | None,
) -> dict[str, str]:
    vals: dict[str, str] = {}
    if sites:
        for s in sites:
            args = s.get("args", [])
            for i, p in enumerate(params):
                if p != boundary_var and i < len(args):
                    vals[p] = str(args[i])
    return vals


def _build_call(
    fname: str, params: list[str], boundary_var: str | None,
    boundary_val: Any, other_vals: dict[str, str],
) -> str:
    if not params:
        return f"{fname}({boundary_val})"
    args = []
    for p in params:
        if p == boundary_var:
            args.append(str(boundary_val))
        else:
            args.append(other_vals.get(p, "..."))
    return f"{fname}({', '.join(args)})"

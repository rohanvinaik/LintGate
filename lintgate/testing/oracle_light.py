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

import ast
import re
from dataclasses import dataclass
from typing import Any


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
    func_node: ast.FunctionDef | ast.AsyncFunctionDef | None = None,
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
    func_key: str,
    func_node: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> tuple[str, str, list[str]]:
    """Extract (module_path, func_name, params)."""
    mod, fname = func_key.rsplit("::", 1) if "::" in func_key else ("", func_key)
    params: list[str] = []
    if func_node is not None:
        params = [a.arg for a in func_node.args.args if a.arg not in ("self", "cls")]
    return mod, fname, params


def _bare_name(name: str) -> str:
    """Return the final name component from a qualified function/method name."""
    return name.split(".")[-1]


def _import_line(module_path: str, func_name: str) -> str:
    top = func_name.split(".")[0]
    if not module_path:
        return ""
    # Convert filesystem path to Python module path:
    # "lintgate/signal_tunings.py" → "lintgate.signal_tunings"
    mod = module_path.replace("/", ".").replace("\\", ".")
    if mod.endswith(".py"):
        mod = mod[:-3]
    return f"from {mod} import {top}"


def _parse_diff_changes(diff: str) -> list[tuple[str, str]]:
    """Extract changed line pairs from diff_summary."""
    if not diff or "\n+ " not in diff:
        return []
    idx = diff.index("\n+ ")
    orig, mutated = diff[2:idx], diff[idx + 3 :]
    return [
        (ol.strip(), ml.strip())
        for ol, ml in zip(orig.split("\n"), mutated.split("\n"), strict=False)
        if ol.strip() != ml.strip()
    ]


# ── Category generators ───────────────────────────────────────────


def _swap_property(
    _survivor: dict,
    func_key: str,
    func_node: ast.FunctionDef | ast.AsyncFunctionDef | None,
    call_site_inputs: list[dict] | None,
) -> ExecutableProperty:
    """SWAP: f(a,b) != f(b,a) — sound when witness confirms order matters."""
    mod, fname, params = _func_info(func_key, func_node)
    setup = _import_line(mod, fname)

    if len(params) < 2:
        return ExecutableProperty(
            category="SWAP",
            inputs={},
            setup_code=setup,
            assertion_code=f"# SWAP survived but {fname} has <2 params",
            preconditions=[],
            confidence=0.3,
            source_lenses=["mutation"],
            needs_oracle=True,
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
        setup_code=setup,
        assertion_code=assertion,
        preconditions=[f"{params[0]} != {params[1]}", "non-commutative"],
        confidence=0.75 if call_site_inputs else 0.6,
        source_lenses=["mutation"] + (["call_sites"] if call_site_inputs else []),
        needs_oracle=False,
    )


def _boundary_property(
    survivor: dict,
    func_key: str,
    func_node: ast.FunctionDef | ast.AsyncFunctionDef | None,
    call_site_inputs: list[dict] | None,
) -> ExecutableProperty:
    """BOUNDARY: behavior differs at actual predicate boundary pair."""
    mod, fname, params = _func_info(func_key, func_node)
    setup = _import_line(mod, fname)
    diff = survivor.get("diff_summary", "")

    info = _extract_boundary_info(diff)
    if info is None:
        return ExecutableProperty(
            category="BOUNDARY",
            inputs={},
            setup_code=setup,
            assertion_code="# BOUNDARY survived but boundary value not extractable",
            preconditions=[],
            confidence=0.3,
            source_lenses=["mutation"],
            needs_oracle=True,
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
        f"assert result_at != result_before, "
        f'"BOUNDARY at {label}{bval}: {comp} should discriminate"'
    )
    return ExecutableProperty(
        category="BOUNDARY",
        inputs={bvar or (params[0] if params else "x"): bval},
        setup_code=setup,
        assertion_code=assertion,
        preconditions=[f"boundary at {label}{bval} ({comp})"],
        confidence=0.85 if not has_unknowns else 0.5,
        source_lenses=["mutation", "diff_analysis"],
        needs_oracle=has_unknowns,
    )


def _type_property(
    survivor: dict,
    func_key: str,
    func_node: ast.FunctionDef | ast.AsyncFunctionDef | None,
    _call_site_inputs: list[dict] | None,
) -> ExecutableProperty:
    """TYPE: wrong type should raise or produce different behavior."""
    mod, fname, _ = _func_info(func_key, func_node)
    imp = _import_line(mod, fname)
    setup = f"{imp}\nimport pytest" if imp else "import pytest"
    diff = survivor.get("diff_summary", "")
    expected_type = _extract_isinstance_type(diff)

    if not expected_type:
        return ExecutableProperty(
            category="TYPE",
            inputs={},
            setup_code=setup,
            assertion_code=(
                f"with pytest.raises((TypeError, ValueError)):\n"
                f"    {fname}(None)  # TODO: use appropriate invalid type"
            ),
            preconditions=["expected type unknown"],
            confidence=0.4,
            source_lenses=["mutation"],
            needs_oracle=True,
        )

    invalid = _INVALID_FOR_TYPE.get(expected_type, "None")
    assertion = (
        f"# isinstance checks {expected_type} — wrong type should be rejected\n"
        f"with pytest.raises((TypeError, ValueError)):\n"
        f"    {fname}({invalid})"
    )
    return ExecutableProperty(
        category="TYPE",
        inputs={"invalid_type": invalid},
        setup_code=setup,
        assertion_code=assertion,
        preconditions=[f"input must NOT be {expected_type}"],
        confidence=0.7,
        source_lenses=["mutation", "diff_analysis"],
        needs_oracle=False,
    )


def _state_property(
    survivor: dict,
    func_key: str,
    func_node: ast.FunctionDef | ast.AsyncFunctionDef | None,
    call_site_inputs: list[dict] | None,
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
            category="STATE",
            inputs={},
            setup_code=setup,
            assertion_code=assertion,
            preconditions=["function returns a meaningful value"],
            confidence=0.7,
            source_lenses=["mutation"],
            needs_oracle=False,
        )

    # remove_assign: try narrow oracle-free fast path first
    attr = _extract_self_attr(diff)
    rhs_info = _extract_assign_rhs(diff, params) if attr else None
    if attr and rhs_info:
        rhs_kind, rhs_value = rhs_info
        # Extract class name from func_key: "mod::ClassName.method" → "ClassName"
        class_name = fname.split(".")[0] if "." in fname else None
        method_name = _bare_name(fname)
        if class_name:
            class_import = _import_line(mod, class_name)
            if rhs_kind == "param":
                # self.attr = param → assert obj.attr == param_value
                call_args = _call_args_from_sites(call_site_inputs) or "..."
                assertion = (
                    f"obj = {class_name}({call_args})\n"
                    f"obj.{method_name}({call_args})\n"
                    f"assert obj.{attr} == {rhs_value}"
                )
            else:
                # self.attr = literal → assert obj.attr == literal
                call_args = _call_args_from_sites(call_site_inputs) or "..."
                assertion = (
                    f"obj = {class_name}({call_args})\n"
                    f"obj.{method_name}({call_args})\n"
                    f"assert obj.{attr} == {rhs_value}"
                )
            return ExecutableProperty(
                category="STATE",
                inputs={},
                setup_code=class_import,
                assertion_code=assertion,
                preconditions=[f"construct {class_name}"],
                confidence=0.65,
                source_lenses=["mutation", "diff_analysis", "state_fast_path"],
                needs_oracle=False,
            )

    # General remove_assign: oracle-dependent fallback
    hint = f"self.{attr}" if attr else "attribute"
    assertion = (
        f"# STATE: {hint} assignment removed — verify it's set after call\n"
        f"# obj = ClassName(...)\n"
        f"# obj.{fname}(...)\n"
        f"# assert obj.{attr or 'ATTR'} == EXPECTED  # FILL"
    )
    return ExecutableProperty(
        category="STATE",
        inputs={},
        setup_code=setup,
        assertion_code=assertion,
        preconditions=["construct object", f"verify {hint}"],
        confidence=0.4 if attr else 0.2,
        source_lenses=["mutation"] + (["diff_analysis"] if attr else []),
        needs_oracle=True,
    )


def _value_property(
    _survivor: dict,
    func_key: str,
    func_node: ast.FunctionDef | ast.AsyncFunctionDef | None,
    call_site_inputs: list[dict] | None,
) -> ExecutableProperty:
    """VALUE: exact output needed — oracle-dependent.

    When the return type is a dataclass or dict (via to_dict), generates
    per-field assertions instead of a single ``assert result == ...``.
    """
    mod, fname, _ = _func_info(func_key, func_node)
    setup = _import_line(mod, fname)

    call_args = _call_args_from_sites(call_site_inputs) or "..."

    # Try field-enumeration for dataclass returns or to_dict methods
    field_assertions = _enumerate_return_fields(func_key, func_node, fname, call_args)
    if field_assertions:
        # to_dict field-enumeration emits key-existence + set-equality checks
        # which are oracle-free. Dataclass return-type fields use FILL placeholders.
        is_to_dict = _bare_name(fname) == "to_dict"
        return ExecutableProperty(
            category="VALUE",
            inputs={},
            setup_code=setup
            if not is_to_dict
            else "",  # to_dict assertions include their own imports
            assertion_code=field_assertions,
            preconditions=[] if is_to_dict else ["fill in expected value for each field"],
            confidence=0.8 if is_to_dict else 0.5,
            source_lenses=["mutation", "field_enumeration"]
            + (["call_sites"] if call_site_inputs else []),
            needs_oracle=not is_to_dict,
        )

    assertion = f"result = {fname}({call_args})\nassert result == ...  # FILL: expected value"
    return ExecutableProperty(
        category="VALUE",
        inputs={},
        setup_code=setup,
        assertion_code=assertion,
        preconditions=["exact expected value must be determined"],
        confidence=0.3,
        source_lenses=["mutation"] + (["call_sites"] if call_site_inputs else []),
        needs_oracle=True,
    )


def _generic_property(
    survivor: dict,
    _func_key: str,
    _func_node: ast.FunctionDef | ast.AsyncFunctionDef | None,
    _call_site_inputs: list[dict] | None,
) -> ExecutableProperty:
    cat = survivor.get("category", "UNKNOWN")
    return ExecutableProperty(
        category=cat,
        inputs={},
        setup_code="",
        assertion_code=f"# {cat} mutation survived — manual investigation needed",
        preconditions=[],
        confidence=0.2,
        source_lenses=["mutation"],
        needs_oracle=True,
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
        m = re.search(r"(\w+)\s*([<>]=?)\s*(\d+(?:\.\d+)?)", orig)
        if m:
            val = m.group(3)
            return {
                "variable": m.group(1),
                "comparator": m.group(2),
                "boundary_value": float(val) if "." in val else int(val),
            }
    return None


def _extract_isinstance_type(diff: str) -> str | None:
    for orig, _ in _parse_diff_changes(diff):
        m = re.search(r"isinstance\(\s*\w+\s*,\s*(\w+(?:\.\w+)*)", orig)
        if m:
            return m.group(1)
    return None


def _extract_self_attr(diff: str) -> str | None:
    for orig, _ in _parse_diff_changes(diff):
        m = re.search(r"self\.(\w+)\s*=", orig)
        if m:
            return m.group(1)
    return None


def _extract_assign_rhs(diff: str, params: list[str]) -> tuple[str, str] | None:
    """Extract the RHS of a ``self.attr = expr`` when it's a param or literal.

    Returns ("param", param_name) or ("literal", repr_value) for simple
    assignments, None otherwise.  Only handles direct parameter references
    and Python literals (int, float, str, bool, None).
    """
    for orig, _ in _parse_diff_changes(diff):
        m = re.match(r"self\.\w+\s*=\s*(.+)$", orig.strip())
        if not m:
            continue
        rhs = m.group(1).strip()
        # Direct parameter reference
        if rhs in params:
            return ("param", rhs)
        # Literal: int/float
        if re.fullmatch(r"-?\d+(?:\.\d+)?", rhs):
            return ("literal", rhs)
        # Literal: quoted string
        if re.fullmatch(r"""(['"]).*?\1""", rhs):
            return ("literal", rhs)
        # Literal: bool / None
        if rhs in ("True", "False", "None"):
            return ("literal", rhs)
    return None


# ── Input helpers ─────────────────────────────────────────────────


_INVALID_FOR_TYPE: dict[str, str] = {
    "str": "42",
    "int": "'not_int'",
    "float": "'not_float'",
    "bool": "42",
    "list": "42",
    "dict": "42",
    "tuple": "42",
}


def _distinct_values(sites: list[dict] | None) -> tuple[str, str]:
    if sites:
        for s in sites:
            args = s.get("positional_args") or s.get("args") or []
            if len(args) >= 2 and str(args[0]) != str(args[1]):
                return str(args[0]), str(args[1])
    return "1", "2"


def _call_args_from_sites(sites: list[dict] | None) -> str:
    if not sites:
        return ""
    for s in sites:
        ctx = s.get("context", "")
        m = re.search(r"\(([^)]+)\)", ctx)
        if m:
            return m.group(1)
    return ""


def _other_param_values(
    params: list[str],
    boundary_var: str | None,
    sites: list[dict] | None,
) -> dict[str, str]:
    vals: dict[str, str] = {}
    if sites:
        for s in sites:
            args = s.get("positional_args") or s.get("args") or []
            for i, p in enumerate(params):
                if p != boundary_var and i < len(args):
                    vals[p] = str(args[i])
    return vals


def _build_call(
    fname: str,
    params: list[str],
    boundary_var: str | None,
    boundary_val: Any,
    other_vals: dict[str, str],
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


# ── Field-enumeration assertions ─────────────────────────────────


def _enumerate_return_fields(
    func_key: str,
    func_node: ast.FunctionDef | ast.AsyncFunctionDef | None,
    fname: str,
    call_args: str,
) -> str | None:
    """Generate per-field assertions when the return type is a dataclass or dict.

    Detects two patterns:
    1. Return annotation is a known dataclass → enumerate its fields
    2. Method named ``to_dict`` → enumerate dict keys from the parent class fields
    """
    if func_node is None:
        return None

    # Pattern 1: to_dict() method → enumerate parent class dataclass fields
    if _bare_name(fname) == "to_dict":
        return _to_dict_field_assertions(func_key)

    # Pattern 2: Return annotation is a dataclass
    if func_node.returns is None:
        return None

    return_type = ""
    if isinstance(func_node.returns, ast.Name):
        return_type = func_node.returns.id
    elif isinstance(func_node.returns, ast.Attribute):
        return_type = func_node.returns.attr

    if not return_type:
        return None

    try:
        from lintgate.testing.typed_synthesis import _resolve_dataclass
    except ImportError:
        return None

    mod = func_key.rsplit("::", 1)[0] if "::" in func_key else ""
    mod_path = mod.replace("/", ".").replace("\\", ".")
    if mod_path.endswith(".py"):
        mod_path = mod_path[:-3]

    cls = _resolve_dataclass(return_type, mod_path)
    if cls is None:
        return None

    from dataclasses import fields as dc_fields

    fields = dc_fields(cls)
    lines = [f"result = {fname}({call_args})"]
    for f in fields:
        lines.append(f"assert result.{f.name} == ...  # FILL: expected {f.name}")

    return "\n".join(lines)


def _to_dict_field_assertions(
    func_key: str,
) -> str | None:
    """Enumerate dict keys for a to_dict() method from its parent class."""
    # Walk up to find the parent class in the module AST
    mod = func_key.rsplit("::", 1)[0] if "::" in func_key else ""
    mod_path = mod.replace("/", ".").replace("\\", ".")
    if mod_path.endswith(".py"):
        mod_path = mod_path[:-3]

    # Find parent class name from func_key (format: mod.py::ClassName.to_dict)
    parts = func_key.rsplit("::", 1)
    if len(parts) < 2:
        return None
    func_part = parts[1]
    if "." not in func_part:
        return None
    class_name = func_part.split(".")[0]

    try:
        from lintgate.testing.typed_synthesis import _resolve_dataclass
    except ImportError:
        return None

    cls = _resolve_dataclass(class_name, mod_path)
    if cls is None:
        return None

    from dataclasses import fields as dc_fields

    try:
        from lintgate.testing.typed_synthesis import _dataclass_minimal_construction
    except ImportError:
        return None

    ctor_code, ctor_imports = _dataclass_minimal_construction(cls)
    if ctor_code == "None":
        return None

    fields = dc_fields(cls)
    lines = []
    # Include import for the dataclass
    for imp in ctor_imports:
        lines.append(imp)
    lines.append(f"obj = {ctor_code}")
    lines.append("d = obj.to_dict()")
    for f in fields:
        lines.append(f'assert "{f.name}" in d')
    lines.append(f"assert set(d.keys()) == {{{', '.join(repr(f.name) for f in fields)}}}")

    return "\n".join(lines)


# ── Survivor-aware SWAP gating ───────────────────────────────────


# Parameter names that are semantically non-commutative (order matters)
_NON_COMMUTATIVE_PAIRS: set[frozenset[str]] = {
    frozenset({"sigma", "risk_score"}),
    frozenset({"start", "end"}),
    frozenset({"left", "right"}),
    frozenset({"source", "target"}),
    frozenset({"input", "output"}),
    frozenset({"expected", "actual"}),
    frozenset({"key", "value"}),
    frozenset({"numerator", "denominator"}),
    frozenset({"min", "max"}),
    frozenset({"before", "after"}),
    frozenset({"old", "new"}),
    frozenset({"from_val", "to_val"}),
}


def should_emit_swap_test(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef | None,
    survivors: list[dict] | None = None,
) -> bool:
    """Decide whether to emit a cross-parameter swap test.

    Only emits when one of these is true:
    1. A real SWAP survivor exists in the mutation profile
    2. Parameter names are semantically distinct and non-commutative
    3. Another lens (e.g. oracle-light) already suggests order sensitivity

    Does NOT emit for generic "2+ numeric params" — too many false positives.
    """
    # Gate 1: SWAP survivor exists
    if survivors:
        for s in survivors:
            if s.get("category") == "SWAP":
                return True

    # Gate 2: Semantically non-commutative parameter pairs
    if func_node is not None:
        params = [a.arg for a in func_node.args.args if a.arg not in ("self", "cls")]
        if len(params) >= 2:
            param_set = frozenset(params[:2])
            if param_set in _NON_COMMUTATIVE_PAIRS:
                return True
            # Check for prefix-based non-commutativity
            # e.g. (static_spec_level, empirical_spec_level) — distinct prefixes
            if _params_have_distinct_prefixes(params[0], params[1]):
                return True

    return False


def _params_have_distinct_prefixes(a: str, b: str) -> bool:
    """Check if two parameter names share a suffix but have different prefixes.

    e.g. static_spec_level vs empirical_spec_level → different prefixes, same suffix
    This strongly suggests order matters.
    """
    parts_a = a.split("_")
    parts_b = b.split("_")
    if len(parts_a) < 2 or len(parts_b) < 2:
        return False
    # Share at least one trailing segment
    return parts_a[-1] == parts_b[-1] and parts_a[0] != parts_b[0]


# ── Round-trip pair detection ────────────────────────────────────


def detect_round_trip_pairs(
    source_file: str,
) -> list[tuple[str, str, str]]:
    """Detect to_dict/from_dict pairs in a source file.

    Returns list of (class_name, serialize_method, deserialize_func) tuples.
    Patterns detected:
    - Class.to_dict() + _item_from_dict(d) / ClassName.from_dict(d)
    - Class.to_dict() + module-level _from_dict / _parse_dict functions
    """
    try:
        with open(source_file, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=source_file)
    except (OSError, SyntaxError):
        return []

    pairs: list[tuple[str, str, str]] = []
    classes_with_to_dict: list[str] = []
    module_deserializers: list[tuple[str, set[str]]] = []

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    if item.name == "to_dict":
                        classes_with_to_dict.append(node.name)
                    elif item.name == "from_dict":
                        # classmethod from_dict on the same class
                        pairs.append((node.name, "to_dict", f"{node.name}.from_dict"))

        elif isinstance(node, ast.FunctionDef):
            name = node.name
            if "from_dict" in name or "from_d" in name or "_parse_dict" in name:
                module_deserializers.append((name, _returned_constructor_names(node)))

    # Match classes with to_dict to module-level deserializers
    for cls_name in classes_with_to_dict:
        # Already has a class-level from_dict?
        if any(p[0] == cls_name and "from_dict" in p[2] for p in pairs):
            continue
        # Look for _item_from_dict, _<classname>_from_dict, etc.
        cls_lower = cls_name.lower()
        for deser, returned_classes in module_deserializers:
            deser_lower = deser.lower()
            if cls_lower in deser_lower or cls_name in returned_classes:
                pairs.append((cls_name, "to_dict", deser))
                break

    return pairs


def generate_round_trip_test(
    class_name: str,
    serialize_method: str,
    deserialize_func: str,
    module_path: str,
) -> ExecutableProperty:
    """Generate a round-trip test for a serialize/deserialize pair.

    Produces an executable assertion that constructs an instance, serializes,
    deserializes, and compares field-by-field.
    """
    try:
        from lintgate.testing.typed_synthesis import _resolve_dataclass, synthesize_value
    except ImportError:
        return _round_trip_fallback(class_name, serialize_method, deserialize_func, module_path)

    mod_path = module_path.replace("/", ".").replace("\\", ".")
    if mod_path.endswith(".py"):
        mod_path = mod_path[:-3]

    cls = _resolve_dataclass(class_name, mod_path)
    if cls is None:
        return _round_trip_fallback(class_name, serialize_method, deserialize_func, module_path)

    from dataclasses import MISSING
    from dataclasses import fields as dc_fields

    fields = dc_fields(cls)
    # Build constructor with non-default values that differ from defaults
    ctor_args: list[str] = []
    for f in fields:
        if f.default is not MISSING or f.default_factory is not MISSING:
            synth = synthesize_value(str(f.type) if f.type else "", f.name, mod_path)
            ctor_args.append(f"{f.name}={synth.code}")
        else:
            synth = synthesize_value(str(f.type) if f.type else "", f.name, mod_path)
            ctor_args.append(f"{f.name}={synth.code}")

    is_classmethod = "." in deserialize_func
    deser_name = deserialize_func.split(".")[-1] if is_classmethod else deserialize_func

    setup = f"from {mod_path} import {class_name}"
    if not is_classmethod:
        setup += f", {deser_name}"

    ctor_call = f"{class_name}({', '.join(ctor_args)})"
    deser_call = f"{class_name}.{deser_name}" if is_classmethod else deser_name

    # Build field comparisons
    field_asserts = []
    for f in fields:
        field_asserts.append(
            f'assert reconstructed.{f.name} == original.{f.name}, "{f.name} mismatch"'
        )

    assertion = (
        f"original = {ctor_call}\n"
        f"serialized = original.{serialize_method}()\n"
        f"reconstructed = {deser_call}(serialized)\n" + "\n".join(field_asserts)
    )

    return ExecutableProperty(
        category="ROUND_TRIP",
        inputs={},
        setup_code=setup,
        assertion_code=assertion,
        preconditions=[f"{class_name}.{serialize_method} ↔ {deserialize_func}"],
        confidence=0.9,
        source_lenses=["pair_detection", "typed_synthesis"],
        needs_oracle=False,
        function_key=f"{module_path}::{class_name}.{serialize_method}",
    )


def _returned_constructor_names(node: ast.FunctionDef) -> set[str]:
    """Collect class names directly constructed in function return statements."""
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Return):
            continue
        value = child.value
        if not isinstance(value, ast.Call):
            continue
        func = value.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _round_trip_fallback(
    class_name: str,
    serialize_method: str,
    deserialize_func: str,
    module_path: str,
) -> ExecutableProperty:
    """Fallback round-trip test when dataclass introspection fails."""
    mod_path = module_path.replace("/", ".").replace("\\", ".")
    if mod_path.endswith(".py"):
        mod_path = mod_path[:-3]

    return ExecutableProperty(
        category="ROUND_TRIP",
        inputs={},
        setup_code=f"from {mod_path} import {class_name}",
        assertion_code=(
            f"# Round-trip: {class_name}.{serialize_method}() ↔ {deserialize_func}()\n"
            f"# original = {class_name}(...)\n"
            f"# reconstructed = {deserialize_func}(original.{serialize_method}())\n"
            f"# assert reconstructed == original  # FILL"
        ),
        preconditions=["construct instance with non-default values"],
        confidence=0.3,
        source_lenses=["pair_detection"],
        needs_oracle=True,
    )

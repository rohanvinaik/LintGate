"""Characterization-backed oracles with provenance tracking.

Extends golden capture to parametric functions via call-site-inferred
inputs. Characterization is PROVISIONAL unless corroborated by another
lens (purity + determinism, or mutation kills) — otherwise it
fossilizes bugs as spec.
"""

from __future__ import annotations

import ast
import importlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Provenance(str, Enum):
    """Maturity level of a characterization capture."""

    UNCHECKED = "unchecked"
    PROVISIONAL = "provisional"
    CORROBORATED = "corroborated"


@dataclass
class GoldenCapture:
    """A captured golden value for a function invocation."""

    inputs: list[Any]
    kwargs: dict[str, Any] = field(default_factory=dict)
    output: str = ""  # repr of result
    deterministic: bool = False
    provenance: Provenance = Provenance.PROVISIONAL
    corroborating_lens: str = ""


def capture_golden(
    module_path: str,
    function_name: str,
    call_site_inputs: list[dict],
) -> list[GoldenCapture]:
    """Capture golden values using call-site inferred inputs.

    For each call site with evaluable literal args, calls the function
    twice to check determinism and captures the result.
    """
    captures: list[GoldenCapture] = []
    seen_args: set[str] = set()

    # Try zero-arg capture first
    zero = _try_capture(module_path, function_name, [], {})
    if zero is not None:
        captures.append(zero)
        seen_args.add("()")

    # Then try each call site with literal-evaluable args
    for site in call_site_inputs:
        parsed = _eval_call_site(site)
        if parsed is None:
            continue
        args, kwargs = parsed
        key = repr((args, kwargs))
        if key in seen_args:
            continue
        seen_args.add(key)
        capture = _try_capture(module_path, function_name, args, kwargs)
        if capture is not None:
            captures.append(capture)

    return captures


def corroborate_captures(
    captures: list[GoldenCapture],
    mutation_state: dict[str, Any] | None = None,
    is_pure: bool = False,
) -> list[GoldenCapture]:
    """Upgrade PROVISIONAL captures to CORROBORATED when evidence supports.

    Corroboration sources:
    1. Pure + deterministic: no side effects and reproducible
    2. VALUE mutations killed: golden value discriminates correct from mutant
    """
    if not captures:
        return captures

    value_killed = False
    if mutation_state:
        killed = mutation_state.get("killed_records", [])
        value_killed = any(r.get("category") == "VALUE" for r in killed)

    result: list[GoldenCapture] = []
    for cap in captures:
        if cap.provenance != Provenance.PROVISIONAL:
            result.append(cap)
            continue

        if cap.deterministic and is_pure:
            result.append(GoldenCapture(
                inputs=cap.inputs, kwargs=cap.kwargs,
                output=cap.output, deterministic=cap.deterministic,
                provenance=Provenance.CORROBORATED,
                corroborating_lens="pure_deterministic",
            ))
        elif value_killed:
            result.append(GoldenCapture(
                inputs=cap.inputs, kwargs=cap.kwargs,
                output=cap.output, deterministic=cap.deterministic,
                provenance=Provenance.CORROBORATED,
                corroborating_lens="mutation_value_killed",
            ))
        else:
            result.append(cap)

    return result


def generate_golden_test(
    func_key: str,
    captures: list[GoldenCapture],
) -> str:
    """Generate test code from golden captures with provenance comments."""
    if not captures:
        return ""

    mod, fname = func_key.rsplit("::", 1) if "::" in func_key else ("", func_key)

    lines: list[str] = []
    if mod:
        lines.append(f"from {mod} import {fname}")
        lines.append("")

    for i, cap in enumerate(captures):
        suffix = f"_{i}" if len(captures) > 1 else ""
        test_name = f"test_{fname}_golden{suffix}"

        args_str = ", ".join(repr(a) for a in cap.inputs)
        if cap.kwargs:
            kw_parts = [f"{k}={v!r}" for k, v in cap.kwargs.items()]
            args_str = ", ".join([args_str, *kw_parts]) if args_str else ", ".join(kw_parts)

        lines.append(f"def {test_name}():")
        if cap.provenance == Provenance.PROVISIONAL:
            lines.append(
                '    """Golden capture — PROVISIONAL (may fossilize bugs)."""'
            )
        elif cap.provenance == Provenance.CORROBORATED:
            lines.append(
                f'    """Golden capture — corroborated via {cap.corroborating_lens}."""'
            )
        else:
            lines.append('    """Golden capture — unchecked."""')

        prov_tag = (
            f"  # {cap.provenance.value}"
            if cap.provenance != Provenance.CORROBORATED
            else ""
        )
        if cap.deterministic:
            lines.append(f"    result = {fname}({args_str})")
            lines.append(f"    assert repr(result) == {cap.output!r}{prov_tag}")
        else:
            lines.append(f"    result = {fname}({args_str})")
            lines.append(
                f"    assert result is not None  # non-deterministic{prov_tag}"
            )
        lines.append("")

    return "\n".join(lines)


# ── Internals ─────────────────────────────────────────────────────


def _try_capture(
    module_path: str,
    function_name: str,
    args: list[Any],
    kwargs: dict[str, Any],
) -> GoldenCapture | None:
    """Try to capture a golden value by calling the function twice."""
    try:
        mod = importlib.import_module(module_path)
        func = getattr(mod, function_name)
        result1 = func(*args, **kwargs)
        result2 = func(*args, **kwargs)
        repr1, repr2 = repr(result1), repr(result2)
        return GoldenCapture(
            inputs=list(args), kwargs=dict(kwargs),
            output=repr1, deterministic=(repr1 == repr2),
        )
    except Exception:
        return None


def _eval_call_site(site: dict) -> tuple[list[Any], dict[str, Any]] | None:
    """Try to evaluate all args in a call site as Python literals.

    Returns None if any arg is a variable name or non-literal expression.
    """
    args: list[Any] = []
    for a in site.get("args", []):
        if isinstance(a, str):
            try:
                args.append(ast.literal_eval(a))
            except (ValueError, SyntaxError):
                return None
        else:
            args.append(a)

    kwargs: dict[str, Any] = {}
    for k, v in site.get("kwargs", {}).items():
        if isinstance(v, str):
            try:
                kwargs[k] = ast.literal_eval(v)
            except (ValueError, SyntaxError):
                return None
        else:
            kwargs[k] = v

    return args, kwargs

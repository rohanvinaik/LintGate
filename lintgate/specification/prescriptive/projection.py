"""Slim per-function projection for prescriptive retrospective compose.

Persists fan_in, fan_out, coupling_surface, covering_tests, priority_band,
and neighbor keys during spec/controlplane runs. Retrospective compose
reads this cheaply instead of rebuilding manifests.

Storage: .lintgate/spec_cache/prescriptive_projection.json
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class FunctionProjection:
    """Slim graph projection for one function."""

    function_key: str = ""
    fan_in: int = 0
    fan_out: int = 0
    coupling_surface: int = 0
    covering_tests: list[str] = field(default_factory=list)
    priority_band: str = "P2"
    neighbor_keys: list[str] = field(default_factory=list)
    is_pure: bool = False
    estimated_sigma: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FunctionProjection:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


_PROJECTION_FILE = ".lintgate/spec_cache/prescriptive_projection.json"


def save_projection(project_root: str, projections: dict[str, FunctionProjection]) -> None:
    """Save all function projections to disk."""
    path = os.path.join(project_root, _PROJECTION_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({k: v.to_dict() for k, v in projections.items()}, f, indent=2)


def load_projection(project_root: str) -> dict[str, FunctionProjection]:
    """Load all function projections from disk."""
    path = os.path.join(project_root, _PROJECTION_FILE)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {k: FunctionProjection.from_dict(v) for k, v in data.items()}
    except (OSError, ValueError):
        return {}


def load_single_projection(project_root: str, function_key: str) -> FunctionProjection | None:
    """Load projection for a single function (reads full file, but fast for small caches)."""
    all_proj = load_projection(project_root)
    return all_proj.get(function_key)


def _build_reverse_graph(call_graph: dict[str, list[str]]) -> dict[str, list[str]]:
    """Build a reverse call graph mapping each callee to its callers.

    Pure helper — no I/O, no side effects.
    """
    reverse: dict[str, list[str]] = {}
    for caller, callees in call_graph.items():
        for callee in callees:
            reverse.setdefault(callee, []).append(caller)
    return reverse


def _assemble_projection(
    func_key: str,
    fd: dict[str, Any],
    fan_in_keys: list[str],
    fan_out_keys: list[str],
) -> FunctionProjection:
    """Assemble a single FunctionProjection from ledger data and graph context.

    Pure function, no I/O.
    """
    neighbors = list(set(fan_in_keys + fan_out_keys))[:20]
    return FunctionProjection(
        function_key=func_key,
        fan_in=len(fan_in_keys),
        fan_out=len(fan_out_keys),
        coupling_surface=fd.get("coupling_surface", 0),
        covering_tests=fd.get("covering_tests", [])[:10],
        priority_band=fd.get("priority_band", "P2"),
        neighbor_keys=neighbors,
        is_pure=fd.get("is_pure", False),
        estimated_sigma=fd.get("estimated_sigma", 0),
    )


def build_projection_from_ledger(
    ledger_data: dict[str, dict[str, Any]],
    call_graph: dict[str, list[str]] | None = None,
) -> dict[str, FunctionProjection]:
    """Build projections from a ledger dict + optional call graph.

    ledger_data: function_key → flat dict (from SpecificationLedger.to_dict()["functions"])
    call_graph: function_key → [callee_keys] (from build_cross_module_call_graph)
    """
    reverse_graph = _build_reverse_graph(call_graph) if call_graph else {}

    projections: dict[str, FunctionProjection] = {}
    for func_key, fd in ledger_data.items():
        fan_out_keys = call_graph.get(func_key, []) if call_graph else []
        fan_in_keys = reverse_graph.get(func_key, []) if call_graph else []
        projections[func_key] = _assemble_projection(func_key, fd, fan_in_keys, fan_out_keys)

    return projections

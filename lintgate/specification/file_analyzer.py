"""Single-file specification analyzer — build spec data for one file at a time.

Avoids full-project discovery. Builds manifests, ledger, and prescriptions
scoped to a single source file. Designed for interactive MCP use where
analyzing one file should be fast and resource-bounded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FileSpecResult:
    """Result of analyzing a single file's specification state."""

    file: str
    project_root: str
    functions: dict[str, Any] = field(default_factory=dict)
    prescriptions: list[dict[str, Any]] = field(default_factory=list)
    total_sigma: int = 0
    mean_spec_level: float = 0.0
    regime_distribution: dict[str, int] = field(default_factory=dict)
    risk_distribution: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "file": self.file,
            "project_root": self.project_root,
            "total_functions": len(self.functions),
            "total_sigma": self.total_sigma,
            "mean_spec_level": round(self.mean_spec_level, 3),
            "regime_distribution": self.regime_distribution,
            "risk_distribution": self.risk_distribution,
            "functions": self.functions,
        }
        if self.prescriptions:
            d["prescriptions"] = self.prescriptions
        if self.error:
            d["error"] = self.error
        return d


def analyze_file(
    file_path: str,
    project_root: str,
    include_prescriptions: bool = False,
    max_prescriptions: int = 10,
) -> FileSpecResult:
    """Analyze specification complexity for a single Python file.

    Args:
        file_path: Absolute path to the Python source file.
        project_root: Project root for import/key resolution.
        include_prescriptions: Whether to generate test prescriptions.
        max_prescriptions: Max prescriptions per function.

    Returns:
        FileSpecResult with per-function spec data.
    """
    result = FileSpecResult(
        file=os.path.relpath(file_path, project_root),
        project_root=project_root,
    )

    if not os.path.isfile(file_path):
        result.error = f"File not found: {file_path}"
        return result

    if not file_path.endswith(".py"):
        result.error = "Not a Python file"
        return result

    try:
        return _do_analyze(file_path, project_root, include_prescriptions, max_prescriptions, result)
    except Exception as e:
        result.error = f"Analysis failed: {e}"
        return result


def _do_analyze(
    file_path: str,
    project_root: str,
    include_prescriptions: bool,
    max_prescriptions: int,
    result: FileSpecResult,
) -> FileSpecResult:
    """Core analysis logic, separated for clean error handling."""
    from lintgate.linters.performance_checks.manifest import build_manifest
    from lintgate.linters.test_effectiveness.manifest import build_test_effectiveness_manifest
    from lintgate.specification.call_graph import build_cross_module_call_graph
    from lintgate.specification.ledger import build_specification_ledger

    py_files = [file_path]

    # Build manifests scoped to this single file
    prop_manifest = build_manifest(project_root, py_files)
    teff_manifest = build_test_effectiveness_manifest(project_root, py_files)
    call_graph = build_cross_module_call_graph(py_files, project_root)

    ledger = build_specification_ledger(
        prop_manifest, teff_manifest, project_root,
        py_files=py_files, call_graph=call_graph,
    )

    if not ledger.functions:
        return result

    # Build per-function output
    total_spec = 0.0
    for key, fs in ledger.functions.items():
        result.functions[key] = {
            "sigma": fs.core.estimated_sigma,
            "sigma_confidence": round(fs.core.sigma_confidence, 3),
            "regime": fs.core.regime,
            "specification_level": round(fs.core.specification_level, 3),
            "phase": fs.core.phase,
            "is_pure": fs.core.is_pure,
            "risk_score": round(fs.risk.risk_score, 3),
            "priority_band": fs.risk.priority_band,
            "testability_score": round(fs.testability.testability_score, 3),
            "design_signals": {
                "boundary_points": fs.design_signals.boundary_points,
                "equivalence_partitions": fs.design_signals.equivalence_partitions,
                "decision_rule_count": fs.design_signals.decision_rule_count,
                "predicate_effect_links": fs.design_signals.predicate_effect_links,
            },
            "optimization_hints": fs.optimization_hints,
            "stop_criteria_met": fs.stop_criteria_met,
        }
        result.total_sigma += fs.core.estimated_sigma
        total_spec += fs.core.specification_level

        regime = fs.core.regime
        result.regime_distribution[regime] = result.regime_distribution.get(regime, 0) + 1
        band = fs.risk.priority_band
        result.risk_distribution[band] = result.risk_distribution.get(band, 0) + 1

    if result.functions:
        result.mean_spec_level = total_spec / len(result.functions)

    # Prescriptions
    if include_prescriptions:
        from lintgate.specification.prescriptions import prescribe

        for _key, fs in ledger.functions.items():
            rxs = prescribe(fs, max_prescriptions=max_prescriptions)
            for rx in rxs:
                result.prescriptions.append({
                    "function": rx.function_key,
                    "kind": rx.prescription_kind,
                    "description": rx.description,
                    "info_gain": round(rx.estimated_info_gain, 3),
                    "priority_band": rx.priority_band,
                    "uncovered_dimension": rx.uncovered_dimension,
                    "suggested_assertion": rx.suggested_assertion,
                })

        band_order = {"P0": 0, "P1": 1, "P2": 2}
        result.prescriptions.sort(
            key=lambda p: (band_order.get(p["priority_band"], 3), -p["info_gain"])
        )

    return result

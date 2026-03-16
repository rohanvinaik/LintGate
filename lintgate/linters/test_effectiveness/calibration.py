"""Calibration infrastructure for assertion strength weights.

Derived from mutation testing outcomes to provide evidence-based scoring.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .types import STRENGTH_MAP, AssertionKind

CALIBRATION_FILE = ".lintgate/calibration.json"


def get_mutation_data_hash(survivors_path: str) -> str:
    """Generate a SHA-256 hash for the mutation survivors file."""
    if not os.path.exists(survivors_path):
        return ""
    try:
        content = Path(survivors_path).read_bytes()
        return hashlib.sha256(content).hexdigest()
    except OSError:
        return ""


def _compute_kind_stats(
    manifest: Any, vulnerable_functions: set[str]
) -> dict[AssertionKind, dict[str, int]]:
    """Count killed/total per AssertionKind, split by present/absent."""
    stats: dict[AssertionKind, dict[str, int]] = {
        kind: {"present_killed": 0, "present_total": 0, "absent_killed": 0, "absent_total": 0}
        for kind in AssertionKind
    }
    for func_name, fe in manifest.functions.items():
        base_name = func_name.split(".")[-1]
        is_killed = base_name not in vulnerable_functions
        present_kinds = {a.kind for a in fe.assertions}
        for kind in AssertionKind:
            bucket = "present" if kind in present_kinds else "absent"
            stats[kind][f"{bucket}_total"] += 1
            if is_killed:
                stats[kind][f"{bucket}_killed"] += 1
    return stats


def _apply_marginal_contributions(
    kind_stats: dict[AssertionKind, dict[str, int]],
) -> dict[AssertionKind, float]:
    """Compute calibrated weights from marginal kill-rate contributions."""
    calibrated = STRENGTH_MAP.copy()
    for kind, stats in kind_stats.items():
        if stats["present_total"] > 0 and stats["absent_total"] > 0:
            p_present = stats["present_killed"] / stats["present_total"]
            p_absent = stats["absent_killed"] / stats["absent_total"]
            delta = (p_present - p_absent) * 0.4
            calibrated[kind] = max(0.1, min(0.95, round(calibrated[kind] + delta, 3)))
    return calibrated


def calibrate_weights(
    project_root: str, survivors_path: str, manifest: Any
) -> dict[AssertionKind, float]:
    """
    Compute calibrated assertion weights using a marginal contribution algorithm.

    Algorithm:
    1. Group functions into 'killed' (no survivors) and 'vulnerable' (has survivors).
    2. For each AssertionKind:
       - Calculate P(kill | kind_present) = (count of killed funcs with kind) / (total funcs with kind)
       - Calculate P(kill | kind_absent) = (count of killed funcs without kind) / (total funcs without kind)
       - Contribution = P(kill | kind_present) - P(kill | kind_absent)
    3. Normalize contribution to [0.1, 0.95] range.
    """
    if not os.path.exists(survivors_path) or not manifest.functions:
        return STRENGTH_MAP.copy()

    # Load survivors and map to functions
    try:
        with open(survivors_path) as f:
            survivors_data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return STRENGTH_MAP.copy()

    # Normalize survivors to function names
    vulnerable_functions = set()
    for s in survivors_data:
        if isinstance(s, dict) and s.get("function"):
            vulnerable_functions.add(s["function"])

    kind_stats = _compute_kind_stats(manifest, vulnerable_functions)
    calibrated_map = _apply_marginal_contributions(kind_stats)

    return calibrated_map


def save_calibration(
    project_root: str, weights: dict[AssertionKind, float], source_hash: str
) -> None:
    """Persist calibrated weights to .lintgate/calibration.json."""
    lintgate_dir = os.path.join(project_root, ".lintgate")
    os.makedirs(lintgate_dir, exist_ok=True)

    data = {
        "source_hash": source_hash,
        "weights": {k.value: v for k, v in weights.items()},
    }

    with open(os.path.join(project_root, CALIBRATION_FILE), "w") as f:
        json.dump(data, f, indent=2)


def get_effective_weights(project_root: str, survivors_path: str) -> dict[AssertionKind, float]:
    """Load calibrated weights if valid and hash matches, else return default."""
    cal_path = os.path.join(project_root, CALIBRATION_FILE)
    if not os.path.exists(cal_path):
        return STRENGTH_MAP

    try:
        with open(cal_path) as f:
            data = json.load(f)

        stored_hash = data.get("source_hash")
        current_hash = get_mutation_data_hash(survivors_path)

        if stored_hash and stored_hash == current_hash:
            weights_data = data.get("weights", {})
            return {AssertionKind(k): v for k, v in weights_data.items()}
        else:
            # Hash mismatch or missing
            return STRENGTH_MAP
    except (json.JSONDecodeError, OSError, ValueError):
        return STRENGTH_MAP

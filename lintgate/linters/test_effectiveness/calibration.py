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

    kind_stats: dict[AssertionKind, dict[str, int]] = {
        kind: {
            "present_killed": 0,
            "present_total": 0,
            "absent_killed": 0,
            "absent_total": 0,
        }
        for kind in AssertionKind
    }

    for func_name, fe in manifest.functions.items():
        base_name = func_name.split(".")[-1]
        is_killed = base_name not in vulnerable_functions

        present_kinds = {a.kind for a in fe.assertions}

        for kind in AssertionKind:
            if kind in present_kinds:
                kind_stats[kind]["present_total"] += 1
                if is_killed:
                    kind_stats[kind]["present_killed"] += 1
            else:
                kind_stats[kind]["absent_total"] += 1
                if is_killed:
                    kind_stats[kind]["absent_killed"] += 1

    calibrated_map = STRENGTH_MAP.copy()

    for kind, stats in kind_stats.items():
        if stats["present_total"] > 0 and stats["absent_total"] > 0:
            p_kill_present = stats["present_killed"] / stats["present_total"]
            p_kill_absent = stats["absent_killed"] / stats["absent_total"]
            contribution = p_kill_present - p_kill_absent

            # Map contribution to weight delta
            # Baseline is current STRENGTH_MAP. Adjust by contribution.
            # This is a heuristic: if kind presence significantly increases kill probability, boost it.
            # Clamp result between 0.1 and 0.95.
            current = calibrated_map[kind]
            # Contribution ranges from -1.0 to 1.0.
            # We scale it: a 0.5 contribution (50% better kill rate) boosts weight by 0.2.
            delta = contribution * 0.4
            calibrated_map[kind] = max(0.1, min(0.95, round(current + delta, 3)))

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


def get_effective_weights(
    project_root: str, survivors_path: str
) -> dict[AssertionKind, float]:
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

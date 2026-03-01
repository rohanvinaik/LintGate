"""Compass I/O — persistence and migration for the compass data model.

Handles YAML load/save, theory profile migration, and compass reset.
Separated from compass.py to keep the data model module focused on
types and pure computation.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .compass import (
    AXIS_NAMES,
    COMPASS_PATH,
    FACET_TO_AXIS,
    CompassAxis,
    CompassClaim,
    CompassDirective,
    CompassState,
    _has_causal_marker,
    _has_contrastive_marker,
    compute_axis_depth,
    compute_gap_report,
)

try:
    import yaml

    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False


# ── Persistence ──────────────────────────────────────────────────────


def load_compass(project_root: str) -> CompassState | None:
    """Load compass state from .claude/compass.yaml.

    Returns None if file doesn't exist or can't be parsed.
    """
    if not _YAML_AVAILABLE:
        return None
    path = Path(project_root) / COMPASS_PATH
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return None
        state = CompassState.from_dict(data)
        # Runtime validation vs schema
        if not state.axes:
            return None
        return state
    except Exception:
        return None


def save_compass(project_root: str, state: CompassState) -> Path:
    """Save compass state to .claude/compass.yaml.

    Updates forged_at timestamp, performs schema validation, and creates
    .claude/ directory if needed. Returns the written path.
    """
    if not _YAML_AVAILABLE:
        msg = "PyYAML is required for compass persistence"
        raise RuntimeError(msg)

    # Hardening: Update timestamp and validate basic invariants
    state.forged_at = time.time()
    if not state.axes:
        msg = "Refusing to save empty CompassState (must have axes)"
        raise ValueError(msg)

    path = Path(project_root) / COMPASS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(
            state.to_dict(),
            f,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
    return path


def reset_compass(project_root: str) -> str | None:
    """Delete compass.yaml. Returns the deleted path, or None if not found."""
    path = Path(project_root) / COMPASS_PATH
    if path.exists():
        path.unlink()
        return str(path)
    return None


# ── Migration Helpers ────────────────────────────────────────────────


def _map_facet_claims(
    facets: dict[str, Any],
    axes: dict[str, CompassAxis],
) -> None:
    """Map 7-facet theory claims into 4-axis compass claims in place."""
    for facet_name, axis_name in FACET_TO_AXIS.items():
        facet_data = facets.get(facet_name, {})
        if not isinstance(facet_data, dict):
            continue
        for raw in facet_data.get("claims", []):
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("claim", raw.get("text", ""))).strip()
            if not text:
                continue
            axes[axis_name].claims.append(
                CompassClaim(
                    text=text,
                    source=str(raw.get("source", "")),
                    heading=str(raw.get("heading", "")),
                    confidence=float(raw.get("confidence", 1.0)),
                    provenance="parsed",
                    origin_facet=facet_name,
                )
            )


def _extract_text_from_item(item: Any, *keys: str) -> str:
    """Pull text from a dict/str item, trying keys in order."""
    if isinstance(item, dict):
        for k in keys:
            val = str(item.get(k, "")).strip()
            if val:
                return val
        return ""
    if isinstance(item, str):
        return item.strip()
    return ""


def _derive_directives(
    full_result: dict[str, Any],
    solution_claims: list[CompassClaim],
) -> list[CompassDirective]:
    """Build toward/away/forbidden directives from theory result."""
    directives: list[CompassDirective] = []

    for ap in full_result.get("anti_patterns", []):
        text = _extract_text_from_item(ap, "pattern", "text")
        if text:
            directives.append(
                CompassDirective(kind="away", text=text, source="anti_patterns")
            )

    for rule in full_result.get("enforceable_rules", []):
        text = _extract_text_from_item(rule, "pattern", "text")
        rule_type = rule.get("type", "forbid") if isinstance(rule, dict) else "forbid"
        if text:
            kind = "forbidden" if rule_type == "forbid" else "toward"
            directives.append(
                CompassDirective(kind=kind, text=text, source="enforceable_rules")
            )

    for claim in solution_claims[:5]:
        if claim.origin_facet in ("problem_solving", "architecture"):
            directives.append(
                CompassDirective(kind="toward", text=claim.text, source="solution")
            )

    return directives


def _score_axes(axes: dict[str, CompassAxis]) -> None:
    """Score depths and set summaries on each axis in place."""
    for axis in axes.values():
        axis.depth = compute_axis_depth(axis.claims)
        if axis.claims:
            scored = sorted(
                axis.claims,
                key=lambda c: (
                    c.confidence,
                    _has_causal_marker(c.text) or _has_contrastive_marker(c.text),
                ),
                reverse=True,
            )
            axis.summary = scored[0].text


# ── Migration ────────────────────────────────────────────────────────


def migrate_from_theory_profile(
    theory_profile: dict[str, Any],
    full_result: dict[str, Any] | None = None,
) -> CompassState:
    """Convert a 7-facet theory profile to a 4-axis compass state."""
    full_result = full_result or {}
    axes: dict[str, CompassAxis] = {name: CompassAxis(name=name) for name in AXIS_NAMES}
    facets = theory_profile if isinstance(theory_profile, dict) else {}

    _map_facet_claims(facets, axes)
    directives = _derive_directives(full_result, axes["solution"].claims)
    _score_axes(axes)

    state = CompassState(
        version=1,
        axes=axes,
        directives=directives,
        forged_at=time.time(),
    )
    state.gap_report = compute_gap_report(state)
    return state

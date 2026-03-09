"""Contract drift detector — preemptive test adaptation for signature changes.

When a function's return arity, parameter list, or type annotation changes,
this module detects affected test call sites that will break.

Finding code: **TEFF010 — Contract drift: test unpacking mismatch**

Advisory only — emits warnings about test sites that need updating after
a function signature change.  Does NOT auto-fix (auto-fix is opt-in future work).

Implementation split across sub-modules:
- _contract_drift_types.py: data types (SignatureChange, AffectedTestSite,
  ContractDriftResult) and small AST helpers
- _contract_drift_detection.py: detection functions (detect_param_changes,
  detect_return_arity_change, find_affected_test_sites, call-site finders)
"""

from __future__ import annotations

import os

from ._contract_drift_detection import (  # noqa: F401 — re-exports
    _find_call_sites,
    _find_unpack_mismatches,
    _get_call_name,
    detect_param_changes,
    detect_return_arity_change,
    find_affected_test_sites,
)

# ── Sub-module imports ────────────────────────────────────────────────────
# Imported here and re-exported for backward compatibility. All external
# code that does ``from lintgate.channels.contract_drift_detector import X``
# continues to work without changes.
from ._contract_drift_types import (  # noqa: F401 — re-exports
    AffectedTestSite,
    ContractDriftResult,
    SignatureChange,
    _arity_from_annotation,
    _extract_function_params,
    _extract_function_return_arities,
    _filepath_to_module,
    _find_function_line,
)

# ── Orchestration ─────────────────────────────────────────────────────


def analyze_contract_drift(
    filepath: str,
    old_source: str,
    new_source: str,
    test_files: list[str],
) -> list[ContractDriftResult]:
    """Full contract drift analysis: detect changes and find affected tests.

    Returns a list of ContractDriftResult, one per detected change.
    """
    results: list[ContractDriftResult] = []

    changes = detect_return_arity_change(filepath, old_source, new_source)
    changes.extend(detect_param_changes(filepath, old_source, new_source))

    for change in changes:
        affected = find_affected_test_sites(change, test_files)
        advisory = _build_advisory(change, affected)
        results.append(
            ContractDriftResult(
                change=change,
                affected_sites=affected,
                advisory=advisory,
            )
        )

    return results


def _build_advisory(
    change: SignatureChange,
    affected: list[AffectedTestSite],
) -> str:
    """Build a human-readable advisory string."""
    if not affected:
        return ""

    if change.change_type == "return_arity":
        sites_str = ", ".join(f"{os.path.basename(s.test_file)}:{s.line}" for s in affected[:5])
        suffix = f" and {len(affected) - 5} more" if len(affected) > 5 else ""
        return (
            f"{change.function}() return arity: {change.old_value} → {change.new_value}. "
            f"{len(affected)} test site{'s' if len(affected) != 1 else ''} "
            f"unpack{'s' if len(affected) == 1 else ''} {change.old_value} values: "
            f"{sites_str}{suffix}."
        )

    if change.change_type == "param_added":
        old_set = set(change.old_value) if isinstance(change.old_value, list) else set()
        new_set = set(change.new_value) if isinstance(change.new_value, list) else set()
        added = sorted(new_set - old_set)
        return (
            f"{change.function}() gained parameter{'s' if len(added) != 1 else ''}: "
            f"{', '.join(added)}. "
            f"{len(affected)} test call site{'s' if len(affected) != 1 else ''} may need updating."
        )

    if change.change_type == "param_removed":
        old_set = set(change.old_value) if isinstance(change.old_value, list) else set()
        new_set = set(change.new_value) if isinstance(change.new_value, list) else set()
        removed = sorted(old_set - new_set)
        return (
            f"{change.function}() lost parameter{'s' if len(removed) != 1 else ''}: "
            f"{', '.join(removed)}. "
            f"{len(affected)} test call site{'s' if len(affected) != 1 else ''} may need updating."
        )

    return ""

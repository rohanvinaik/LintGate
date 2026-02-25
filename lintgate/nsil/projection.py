"""Projection helpers to create InferenceStateSnapshot from various state sources."""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from lintgate.nsil.state_schema import InferenceStateSnapshot

if TYPE_CHECKING:
    from lintgate.controlplane.session_memory import SessionMemory
    from lintgate.controlplane.types import MeshResult
    from lintgate.mutation.state import MutationStateManager


# Mapping from coherence state to gate status
_COHERENCE_TO_GATE_STATUS = {
    "stable": "pass",
    "isolated": "yellow",
    "coupled": "fail",
    "systemic": "fail",
    "degraded": "degraded",
}

# Mapping from coherence state to risk level
_COHERENCE_TO_RISK = {
    "stable": "low",
    "isolated": "medium",
    "coupled": "high",
    "systemic": "critical",
    "degraded": "high",
}


def project_from_controlplane(mesh_result: "MeshResult | None") -> InferenceStateSnapshot:
    """Project InferenceStateSnapshot from ControlPlane MeshResult."""
    if mesh_result is None:
        return InferenceStateSnapshot()

    coherence = mesh_result.coherence
    gate_status = _COHERENCE_TO_GATE_STATUS.get(coherence.state, "unknown")
    risk_level = _COHERENCE_TO_RISK.get(coherence.state, "unknown")

    # Collect blocking findings from channel results
    blocking_findings: list[str] = []
    for channel_result in mesh_result.channel_results:
        if channel_result.blocking:
            for finding in channel_result.findings[:3]:  # Limit to first 3
                blocking_findings.append(finding.get("message", "")[:100])

    return InferenceStateSnapshot(
        gate_status=gate_status,
        blocking_findings=blocking_findings,
        risk_level=risk_level,
    )


def project_from_session_memory(session: "SessionMemory | None") -> InferenceStateSnapshot:
    """Project InferenceStateSnapshot from SessionMemory."""
    if session is None:
        return InferenceStateSnapshot()

    # Extract active constraints from recent behavior events
    active_constraints: list[str] = []
    prediction_accuracy: float = 0.0

    # Get recent snapshots to find constraints and prediction accuracy
    recent_snapshots = (
        list(session.snapshots.values())[-5:] if hasattr(session, "snapshots") else []
    )

    for snapshot in recent_snapshots:
        if hasattr(snapshot, "behavior_data") and snapshot.behavior_data:
            for event in snapshot.behavior_data:
                if hasattr(event, "behavior_alerts"):
                    active_constraints.extend(event.behavior_alerts[:3])
                if hasattr(event, "prediction_accuracy") and event.prediction_accuracy is not None:
                    prediction_accuracy = max(prediction_accuracy, event.prediction_accuracy)

    # Deduplicate constraints
    active_constraints = list(dict.fromkeys(active_constraints))[:5]

    return InferenceStateSnapshot(
        active_constraints=active_constraints,
        prediction_accuracy=prediction_accuracy if prediction_accuracy > 0 else 0.0,
    )


def project_from_mutation_state(
    state_manager: "MutationStateManager | None",
) -> InferenceStateSnapshot:
    """Project InferenceStateSnapshot from MutationStateManager."""
    if state_manager is None or not state_manager.state:
        return InferenceStateSnapshot()

    # Calculate mutation summary
    total_functions = len(state_manager.state)
    killed_count = 0
    total_mutations = 0

    for func_state in state_manager.state.values():
        if hasattr(func_state, "killed"):
            killed_count += func_state.killed
        if hasattr(func_state, "total"):
            total_mutations += func_state.total

    survival_rate = 0.0
    if total_mutations > 0:
        survival_rate = 1.0 - (killed_count / total_mutations)

    mutation_summary = {
        "total_functions": total_functions,
        "total_mutations": total_mutations,
        "killed": killed_count,
        "survival_rate": round(survival_rate, 2),
    }

    return InferenceStateSnapshot(mutation_summary=mutation_summary)


def load_and_project(
    path: str = ".",
    format: str = "structured_text",  # noqa: A002
    token_budget: int = 500,
) -> dict[str, Any]:
    """Load all state sources and project to InferenceStateSnapshot.

    Returns a dict with snapshot, format, token_budget, truncated_fields, and sources_used.
    """
    project_root = Path(path).resolve()
    snapshot = InferenceStateSnapshot()
    sources_used: dict[str, bool] = {
        "controlplane": False,
        "session_memory": False,
        "mutation_state": False,
    }
    truncated_fields: list[str] = []

    # Try to load ControlPlane result
    try:
        from lintgate.controlplane.session_memory import SessionMemory

        # Try to get latest mesh result from session
        session = SessionMemory.load(project_root)
        if session and session.snapshots:
            # Get most recent mesh result
            latest_snapshot = list(session.snapshots.values())[-1]
            # Project from the snapshot
            controlplane_snapshot = InferenceStateSnapshot(
                gate_status=_COHERENCE_TO_GATE_STATUS.get(
                    latest_snapshot.coherence_state, "unknown"
                ),
                risk_level=_COHERENCE_TO_RISK.get(latest_snapshot.coherence_state, "unknown"),
                blocking_findings=[f"Finding count: {latest_snapshot.finding_count}"]
                if latest_snapshot.finding_count > 0
                else [],
            )
            snapshot = _merge_snapshots(snapshot, controlplane_snapshot)
            sources_used["controlplane"] = True
    except Exception:
        pass  # Missing artifacts should return valid default snapshot

    # Try to load SessionMemory
    try:
        from lintgate.controlplane.session_memory import SessionMemory

        session = SessionMemory.load(project_root)
        if session:
            session_snapshot = project_from_session_memory(session)
            snapshot = _merge_snapshots(snapshot, session_snapshot)
            sources_used["session_memory"] = True
    except Exception:
        pass

    # Try to load MutationState
    try:
        from lintgate.config import MUTATION_CACHE_DIR
        from lintgate.mutation.state import MutationStateManager

        mutation_dir = MUTATION_CACHE_DIR(project_root)
        state_file = mutation_dir / "state.json"
        if state_file.exists():
            manager = MutationStateManager(state_file)
            mutation_snapshot = project_from_mutation_state(manager)
            snapshot = _merge_snapshots(snapshot, mutation_snapshot)
            sources_used["mutation_state"] = True
    except Exception:
        pass

    # Apply budget and determine truncated fields
    serialized = snapshot.serialize_compact(format=format, budget=token_budget)

    # Track which fields were truncated (not present in output)
    try:
        if format == "json_flat":
            import json

            parsed = json.loads(serialized)
            all_fields = {
                "gate_status",
                "blocking_findings",
                "mutation_summary",
                "active_constraints",
                "prediction_accuracy",
                "risk_level",
                "token_count",
            }
            present_fields = set(parsed.keys())
            truncated_fields = list(all_fields - present_fields)
        elif format == "kv_pairs":
            present_keys = set()
            for item in serialized.split(" "):
                if "=" in item:
                    present_keys.add(item.split("=")[0])
            truncated_fields = list(
                {"active_constraints", "mutation_summary", "blocking_findings"} - present_keys
            )
        # structured_text doesn't track truncation the same way
    except Exception:
        pass

    return {
        "snapshot": {
            "gate_status": snapshot.gate_status,
            "risk_level": snapshot.risk_level,
            "token_count": len(serialized.split()),
        },
        "format": format,
        "token_budget": token_budget,
        "truncated_fields": truncated_fields,
        "sources_used": sources_used,
    }


def _merge_snapshots(
    base: InferenceStateSnapshot, update: InferenceStateSnapshot
) -> InferenceStateSnapshot:
    """Merge two snapshots, with update values taking precedence."""
    return InferenceStateSnapshot(
        gate_status=update.gate_status if update.gate_status != "unknown" else base.gate_status,
        blocking_findings=update.blocking_findings or base.blocking_findings,
        mutation_summary=update.mutation_summary or base.mutation_summary,
        active_constraints=update.active_constraints or base.active_constraints,
        prediction_accuracy=update.prediction_accuracy
        if update.prediction_accuracy > 0
        else base.prediction_accuracy,
        risk_level=update.risk_level if update.risk_level != "unknown" else base.risk_level,
        token_count=0,  # Will be recalculated on serialization
    )

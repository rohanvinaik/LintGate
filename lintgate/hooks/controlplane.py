"""ControlPlane session management helpers for the PostToolUse hook.

Handles session setup, global priors, behavior delta application, constraint
proposing, finding index tracking, and post-run session processing.
"""

from __future__ import annotations

import contextlib
import os
import re
from typing import Any

# ── Session telemetry counter helpers ────────────────────────────────

_SESSION_TELEMETRY_UPDATE_CAP = 10
_SESSION_TELEMETRY_COUNTER_KEY = "_model_profile_telem_updates"


# ── Session telemetry management ──────────────────────────────────


def session_telemetry_updates_used(session: Any) -> int:
    """Return telemetry updates applied in the current session."""
    if session is None or not hasattr(session, "behavior_compass"):
        return 0
    bc = session.behavior_compass
    if not isinstance(bc, dict):
        return 0
    value = bc.get(_SESSION_TELEMETRY_COUNTER_KEY, 0)
    return value if isinstance(value, int) and value >= 0 else 0


def can_apply_session_telemetry(session: Any) -> bool:
    """Check whether this session still has telemetry update budget."""
    return session_telemetry_updates_used(session) < _SESSION_TELEMETRY_UPDATE_CAP


def mark_session_telemetry_applied(session: Any) -> None:
    """Increment the per-session telemetry update counter."""
    if session is None or not hasattr(session, "behavior_compass"):
        return
    bc = session.behavior_compass
    if not isinstance(bc, dict):
        return
    bc[_SESSION_TELEMETRY_COUNTER_KEY] = session_telemetry_updates_used(session) + 1


# ── Model key resolution ─────────────────────────────────────────────


def _collect_model_candidates(input_data: dict[str, Any]) -> list[str | None]:
    """Collect model identity candidates from hook payload fields and env vars."""
    candidates: list[str | None] = [
        input_data.get("model"),
        input_data.get("model_id"),
        input_data.get("model_name"),
        input_data.get("assistant_model"),
    ]

    metadata = input_data.get("metadata")
    if isinstance(metadata, dict):
        candidates.extend(
            [
                metadata.get("model"),
                metadata.get("model_id"),
                metadata.get("model_name"),
            ]
        )

    session_meta = input_data.get("session")
    if isinstance(session_meta, dict):
        candidates.extend(
            [
                session_meta.get("model"),
                session_meta.get("model_id"),
                session_meta.get("model_name"),
            ]
        )

    tool_input = input_data.get("tool_input")
    if isinstance(tool_input, dict):
        candidates.extend(
            [
                tool_input.get("model"),
                tool_input.get("model_id"),
            ]
        )

    for env_key in ("LINTGATE_MODEL_ID", "CLAUDE_MODEL", "OPENAI_MODEL", "MODEL"):
        candidates.append(os.environ.get(env_key))

    return candidates


# ── Model identity resolution ─────────────────────────────────────


def resolve_event_model_key(input_data: dict[str, Any]) -> str | None:
    """Resolve model identity from hook payload fields/env vars.

    Returns canonical provider:model key, or None when unavailable/unresolvable.
    """
    from lintgate.controlplane.model.profiles import resolve_model_key

    for raw in _collect_model_candidates(input_data):
        if not isinstance(raw, str) or not raw.strip():
            continue
        canonical = resolve_model_key(raw)
        if canonical:
            return canonical

    return None


def select_telemetry_profile(store: Any, input_data: dict[str, Any]):
    """Pick the exact model profile for telemetry updates.

    Ambiguous fallback (e.g., "most recently updated profile") is intentionally
    disallowed to prevent cross-model contamination.
    """
    model_key = resolve_event_model_key(input_data)
    if not model_key:
        return None
    profile = store.profiles.get(model_key)
    if profile and profile.is_usable():
        return profile
    return None


# ── Global priors ────────────────────────────────────────────────────


def load_global_priors(cp_config: Any) -> dict | None:
    """Load global behavior profile priors if enabled and sufficient data exists."""
    if not (cp_config.global_memory_enabled and cp_config.channel_enabled("behavior")):
        return None
    try:
        from lintgate.controlplane.global_behavior_profile import (
            MIN_SAMPLE_SIZE,
            load_global_profile,
        )

        gp = load_global_profile(ttl_days=cp_config.global_memory_ttl_days)
        if gp.session_count >= MIN_SAMPLE_SIZE:
            return {
                "enabled": True,
                "alpha": cp_config.global_memory_alpha,
                "decay_horizon": cp_config.global_memory_decay_horizon,
                "computed_bias_adjustments": gp.computed_bias_adjustments,
            }
    except Exception:
        pass
    return None


# ── Session setup and gate ───────────────────────────────────────────


def _inject_session_context(
    session: Any,
    event: Any,
    cp_config: Any,
    cwd: str,
    global_priors: dict | None,
) -> None:
    """Inject behavior compass, global priors, and theory profile into event."""
    if session is not None and cp_config.channel_enabled("behavior"):
        event.raw_input["behavior_compass"] = session.behavior_compass

    if global_priors is not None:
        event.raw_input["behavior_global_priors"] = global_priors

    if session is not None and cp_config.inquiry.any_enabled():
        try:
            from lintgate.theory_extractor import extract_theory

            session.theory_profile_cache = extract_theory(cwd).get("theory_profile")
        except Exception:
            session.theory_profile_cache = None

        if session.theory_profile_cache is not None:
            event.raw_input["theory_profile"] = session.theory_profile_cache


def _check_session_gate(
    session: Any,
    cp_config: Any,
    cwd: str,
    tool_name: str,
    channels: list,
) -> str | None:
    """Check session gate readiness and return advisory if not ready."""
    if (
        session is None
        or not cp_config.inquiry.session_gate
        or tool_name not in ("Write", "Edit", "MultiEdit")
        or session.behavior_compass.get("_session_ready", False)
    ):
        return None

    with contextlib.suppress(Exception):
        from lintgate.context_auditor import check_session_readiness

        readiness = check_session_readiness(cwd, theory_profile=session.theory_profile_cache)
        if not readiness.ready:
            channels[:] = [ch for ch in channels if ch.name != "behavior"]
            return (
                f"[Session Advisory] Context not ready for deep supervision. "
                f"Missing: {', '.join(readiness.missing)}. "
                f"{readiness.recommendation}"
            )
        session.behavior_compass["_session_ready"] = True

    return None


# ── Session setup + gate checking ─────────────────────────────────


def setup_session_and_gate(
    cp_config: Any,
    cwd: str,
    tool_name: str,
    event: Any,
    channels: list,
    global_priors: dict | None,
) -> tuple[Any, str | None]:
    """Set up session memory, theory profile, and session gate. Returns (session, advisory)."""
    session = None

    if cp_config.session_memory:
        with contextlib.suppress(Exception):
            from lintgate.controlplane.session_memory import get_or_create_session

            session = get_or_create_session(cwd, cp_config.session_max_age_hours)

    _inject_session_context(session, event, cp_config, cwd, global_priors)
    advisory = _check_session_gate(session, cp_config, cwd, tool_name, channels)

    return session, advisory


# ── Behavior delta application ───────────────────────────────────────


def _apply_compass_delta(session: Any, cr: Any) -> None:
    """Apply behavior compass delta fields (cooldown counters, nudge flags, theory codas)."""
    if "behavior_compass_delta" not in cr.metrics:
        return

    from lintgate.controlplane.session_memory import (
        load_behavior_compass,
        save_behavior_compass,
    )

    delta = cr.metrics["behavior_compass_delta"]
    existing_telem = session_telemetry_updates_used(session)
    bc = load_behavior_compass(session)
    for key in (
        "last_fired",
        "signal_fire_counts",
        "early_nudge_emitted",
        "pending_nudge_signals",
        "pending_nudge_constraint_check_count",
        "nudge_outcomes",
    ):
        if key in delta:
            setattr(bc, key, delta[key])
    save_behavior_compass(session, bc)
    if existing_telem > 0:
        session.behavior_compass[_SESSION_TELEMETRY_COUNTER_KEY] = existing_telem

    if "_theory_recent_codas" in delta:
        existing_codas = session.behavior_compass.get("_theory_recent_codas", {})
        existing_codas.update(delta["_theory_recent_codas"])
        session.behavior_compass["_theory_recent_codas"] = existing_codas


def _apply_global_profile_delta(session: Any, cr: Any, cp_config: Any) -> None:
    """Apply global behavior profile delta if enabled."""
    if not (cp_config.global_memory_enabled and "global_profile_delta" in cr.metrics):
        return
    with contextlib.suppress(Exception):
        from lintgate.controlplane.global_behavior_profile import (
            apply_session_delta,
            load_global_profile,
            save_global_profile,
        )

        gp = load_global_profile(ttl_days=cp_config.global_memory_ttl_days)
        apply_session_delta(
            gp,
            cr.metrics["global_profile_delta"],
            session_id=session.session_id if session else "",
        )
        save_global_profile(gp)


def _apply_model_telemetry(session: Any, input_data: dict) -> None:
    """Refine model profile telemetry from session signal fires."""
    with contextlib.suppress(Exception):
        from lintgate.controlplane.model.profiles import (
            apply_telemetry_update,
            load_profiles,
            save_profiles,
        )

        store = load_profiles()
        active = select_telemetry_profile(store, input_data)
        signal_fires = {}
        event_count = 0
        if session:
            bc_data = session.behavior_compass
            if isinstance(bc_data, dict):
                signal_fires = bc_data.get("signal_fire_counts", {})
                event_count = bc_data.get("event_counter", 0)
        if (
            active is not None
            and signal_fires
            and event_count >= 10
            and can_apply_session_telemetry(session)
        ):
            apply_telemetry_update(active, signal_fires, event_count)
            mark_session_telemetry_applied(session)
            save_profiles(store)


# ── Behavior delta application ────────────────────────────────────


def apply_behavior_delta(
    session: Any,
    cr: Any,
    cp_config: Any,
    input_data: dict,
) -> list[str]:
    """Apply behavior compass delta, global profile delta, and model telemetry from a channel result."""
    snapshot_alerts = [f.kind for f in cr.findings]
    _apply_compass_delta(session, cr)
    _apply_global_profile_delta(session, cr, cp_config)
    _apply_model_telemetry(session, input_data)
    return snapshot_alerts


# ── Snapshot behavior recording ──────────────────────────────────────


def record_snapshot_behavior(
    snapshot: Any,
    tool_name: str,
    tool_input: Any,
    tool_output: Any,
) -> None:
    """Record tool-level behavioral fields on a snapshot."""
    snapshot.behavior.action_type = tool_name.lower()
    if tool_name != "Bash":
        return

    from lintgate.controlplane.behavior_compass import (
        extract_error_sig,
        normalize_command_sig,
    )

    cmd = (
        tool_input.get("command", "")
        if isinstance(tool_input, dict)
        else (tool_input if isinstance(tool_input, str) else "")
    )
    snapshot.behavior.command_signature = normalize_command_sig(cmd)
    snapshot.behavior.error_signature = extract_error_sig(
        tool_output if isinstance(tool_output, str) else ""
    )
    output_str = tool_output if isinstance(tool_output, str) else str(tool_output)
    exit_match = re.search(
        r"(?:exit[_ ]code|exit[_ ]status|exitstatus)[: =]+(\d+)",
        output_str,
        re.IGNORECASE,
    )
    if exit_match:
        snapshot.behavior.exit_code = int(exit_match.group(1))
    elif "error" in output_str.lower() or "failed" in output_str.lower():
        snapshot.behavior.exit_code = 1
    else:
        snapshot.behavior.exit_code = 0


from .controlplane_ops import (  # noqa: F401, E402
    PostProcessContext,
    _record_and_apply_deltas,
    accumulate_session_telemetry,
    extract_finding_indexes,
    post_process_session,
    refresh_runtime_after_run,
    run_constraint_proposer,
    save_run_details,
)

"""ControlPlane details and status implementation.

Extracted from controlplane_tools.py to keep the register() module under 400 lines.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from typing import Any

from ._controlplane_impl_run import _AVAILABLE_CHANNEL_DESCRIPTIONS

# ── controlplane_get_details helpers ────────────────────────────────────


def _filter_channels(channels_dict, channel_filter):
    """Yield (ch_name, ch_data) pairs, filtered by channel name if specified."""
    for ch_name, ch_data in channels_dict.items():
        if channel_filter and ch_name != channel_filter:
            continue
        yield ch_name, ch_data


def _extract_findings(details, channel, severity, max_issues):
    """Extract and filter findings from run details."""
    all_findings = []
    for ch_name, ch_data in _filter_channels(details.get("channels", {}), channel):
        for f in ch_data.get("findings", []):
            if severity and f.get("severity") != severity:
                continue
            all_findings.append({**f, "channel": ch_name})

    result = {
        "total_matching": len(all_findings),
        "findings": all_findings[:max_issues],
    }
    if len(all_findings) > max_issues:
        result["truncated"] = len(all_findings) - max_issues
    return result


def _build_details_next_actions(run_id: str, output: dict[str, Any]) -> list[dict[str, Any]]:
    """Build recommended next actions based on drill-down details."""
    actions = []
    repairs = output.get("repairs", [])
    if repairs:
        safe_count = sum(1 for r in repairs if r.get("safe"))
        if safe_count > 0:
            actions.append(
                {
                    "tool": "controlplane_apply_repairs",
                    "args": {"path": ".", "safe_only": True},
                    "reason": f"Apply {safe_count} safe auto-repairs found in these details.",
                    "priority": 1,
                }
            )

    findings = output.get("findings", [])
    if findings:
        actions.append(
            {
                "tool": "Bash / your file edit tools",
                "reason": "Address the findings listed above by editing the corresponding files.",
                "priority": 2,
            }
        )

    truncate_count = output.get("truncated", 0)
    if truncate_count > 0:
        actions.append(
            {
                "tool": "controlplane_get_details",
                "args": {
                    "run_id": run_id,
                    "max_issues": len(findings) + truncate_count,
                },
                "reason": f"View the remaining {truncate_count} truncated findings.",
                "priority": 3,
            }
        )

    return actions


def _extract_channel_details(details, channel):
    """Extract channel summary info from run details."""
    ch_details: dict[str, Any] = {}
    for ch_name, ch_data in _filter_channels(details.get("channels", {}), channel):
        ch_details[ch_name] = {
            "status": ch_data.get("status"),
            "severity": ch_data.get("severity"),
            "finding_count": len(ch_data.get("findings", [])),
            "duration_ms": ch_data.get("duration_ms"),
            "error": ch_data.get("error"),
        }
    return ch_details


def _extract_repairs(details, channel):
    """Extract repair actions from run details."""
    all_repairs = []
    for _ch_name, ch_data in _filter_channels(details.get("channels", {}), channel):
        all_repairs.extend(ch_data.get("repairs", []))
    return all_repairs


def _extract_evidence(details, channel):
    """Extract metrics/evidence from run details."""
    evidence: dict[str, Any] = {}
    for ch_name, ch_data in _filter_channels(details.get("channels", {}), channel):
        metrics = ch_data.get("metrics", {})
        if metrics:
            evidence[ch_name] = metrics
    return evidence


def _extract_proven_resolutions_from_details(details: dict, channel) -> list[dict]:
    """Extract proven resolutions from persisted run details."""
    resolutions: list[dict] = []
    for ch_name, ch_data in _filter_channels(details.get("channels", {}), channel):
        for f in ch_data.get("findings", []):
            pr = f.get("proven_resolution")
            if pr:
                resolutions.append(
                    {
                        "channel": ch_name,
                        "finding": f.get("kind"),
                        "message": f.get("message"),
                        "resolution": pr.get("repertoire"),
                        "confidence": pr.get("confidence"),
                    }
                )
    return resolutions


def _populate_findings_section(output: dict, details: dict, channel, severity, max_issues) -> None:
    """Populate findings + delegation annotations into output."""
    output.update(_extract_findings(details, channel, severity, max_issues))
    with contextlib.suppress(Exception):
        from lintgate.controlplane.delegation import annotate_findings_with_suitability

        if "findings" in output:
            annotate_findings_with_suitability(output["findings"], details)


_DEFAULT_SECTIONS = frozenset(
    ["findings", "channel_details", "evidence", "repairs", "coherence", "next_actions", "proven_resolutions"]
)


def _populate_coherence(output, details, _channel, _severity, _max_issues, _run_id):
    output["coherence"] = details.get("coherence", {})


def _populate_findings(output, details, channel, severity, max_issues, _run_id):
    _populate_findings_section(output, details, channel, severity, max_issues)


def _populate_channel_details(output, details, channel, _severity, _max_issues, _run_id):
    output["channel_details"] = _extract_channel_details(details, channel)


def _populate_repairs(output, details, channel, _severity, _max_issues, _run_id):
    output["repairs"] = _extract_repairs(details, channel)


def _populate_evidence(output, details, channel, _severity, _max_issues, _run_id):
    evidence = _extract_evidence(details, channel)
    if evidence:
        output["evidence"] = evidence


def _populate_proven_resolutions(output, details, channel, _severity, _max_issues, _run_id):
    resolutions = _extract_proven_resolutions_from_details(details, channel)
    if resolutions:
        output["proven_resolutions"] = resolutions


def _populate_next_actions(output, _details, _channel, _severity, _max_issues, run_id):
    output["next_actions"] = _build_details_next_actions(run_id, output)


# Dispatch table — order matters for next_actions (must run last).
_SECTION_POPULATORS: list[tuple[str, Any]] = [
    ("coherence", _populate_coherence),
    ("findings", _populate_findings),
    ("channel_details", _populate_channel_details),
    ("repairs", _populate_repairs),
    ("evidence", _populate_evidence),
    ("proven_resolutions", _populate_proven_resolutions),
    ("next_actions", _populate_next_actions),
]


def _impl_controlplane_get_details(run_id, channel, severity, max_issues, sections, helpers):
    """Core implementation of controlplane_get_details."""
    from lintgate.state import load_controlplane_run

    details = load_controlplane_run(run_id)
    if details is None:
        raise ValueError(f"No ControlPlane run found with run_id: {run_id}")

    sections_set = set(sections) if sections else _DEFAULT_SECTIONS
    output: dict[str, Any] = {
        "run_id": run_id,
        "duration_ms": details.get("duration_ms", 0),
    }

    for section_name, populator in _SECTION_POPULATORS:
        if section_name in sections_set:
            populator(output, details, channel, severity, max_issues, run_id)

    return helpers["_json_dumps"](output)


# ── controlplane_status helpers ─────────────────────────────────────────


def _build_config_status(cp_config, project_root, helpers):
    """Build status dict from an existing ControlPlane config."""
    status: dict[str, Any] = {
        "controlplane_enabled": cp_config.enabled,
        "latency_budget_ms": cp_config.latency_budget_ms,
        "advisory_default": cp_config.advisory_default,
        "session_memory": cp_config.session_memory,
        "session_max_age_hours": cp_config.session_max_age_hours,
        "constraint_proposal_threshold": cp_config.constraint_proposal_threshold,
        "token_policy": {
            "hook_max_tokens": cp_config.token_policy.hook_max_tokens,
            "include_pass_details": cp_config.token_policy.include_pass_details,
        },
        "channels": {
            name: {
                "enabled": ch.enabled,
                "blocking": ch.blocking,
                "timeout_ms": ch.timeout_ms,
            }
            for name, ch in cp_config.channels.items()
        },
    }

    if cp_config.session_memory:
        status["session"] = _get_session_status(project_root)

    if not cp_config.enabled:
        status["onboarding"] = helpers["_build_onboarding_status"](project_root)

    return status


def _get_session_status(project_root):
    """Load session and return a summary dict, or None."""
    with contextlib.suppress(Exception):
        from lintgate.controlplane.session_memory import load_session

        session = load_session(project_root)
        if session:
            return {
                "session_id": session.session_id,
                "runs": len(session.snapshots),
                "coherence_trajectory": session.coherence_trajectory[-5:],
                "pending_repairs": sum(
                    1 for v in session.repair_outcomes.values() if v == "pending"
                ),
                "proposed_constraints": len(session.proposed_constraints),
                "active_proposals": sum(
                    1 for c in session.proposed_constraints if c.get("status") == "proposed"
                ),
                "transfer_telemetry": {
                    "latest_packet": session.latest_transfer_packet,
                    "packet_age_hours": round((time.time() - session.last_active) / 3600, 2)
                    if session.latest_transfer_packet
                    else None,
                }
                if hasattr(session, "latest_transfer_packet")
                else {},
                "delivery_health": session.delivery_health_summary
                if hasattr(session, "delivery_health_summary")
                else {},
            }
    return None


def _impl_controlplane_status(path, helpers):
    """Core implementation of controlplane_status."""
    from lintgate.config import load_controlplane_config

    project_root = helpers["_validate_project_root"](path) if path else os.getcwd()
    status: dict[str, Any] = {"project": project_root}

    cp_config = load_controlplane_config(project_root)
    if cp_config:
        status.update(_build_config_status(cp_config, project_root, helpers))
    else:
        status["controlplane_enabled"] = False
        status["note"] = "Add 'controlplane: enabled: true' to .claude/lintgate.yaml to enable"
        status["onboarding"] = helpers["_build_onboarding_status"](project_root)

    status["available_channels"] = _AVAILABLE_CHANNEL_DESCRIPTIONS
    return json.dumps(status, indent=2)

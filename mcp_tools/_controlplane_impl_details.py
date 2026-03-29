"""ControlPlane details and status implementation.

Extracted from controlplane_tools.py to keep the register() module under 400 lines.
"""

from __future__ import annotations

import contextlib
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


_EFFORT_DEFAULTS: dict[str, float] = {
    "ruff": 2.0,
    "mypy": 10.0,
    "radon": 15.0,
    "bandit": 20.0,
    "vulture": 5.0,
    "structure": 15.0,
}
_SEV_WEIGHT: dict[str, float] = {"blocking": 3.0, "warning": 2.0, "informational": 1.0}
_ENVIRONMENT_CHANNELS = frozenset({"deps"})
_ENVIRONMENT_LINTERS = frozenset({"pip_audit", "version_checker"})


def _finding_effort(f: dict) -> float:
    """Compute effective effort for a finding, applying fixable discount."""
    effort = f.get("estimated_effort_minutes") or _EFFORT_DEFAULTS.get(f.get("linter", ""), 10.0)
    if f.get("fixable"):
        effort = min(effort, 2.0)
    return effort


def _finding_roi(f: dict) -> float:
    """Compute ROI for a finding dict."""
    effort = _finding_effort(f)
    weight = _SEV_WEIGHT.get(f.get("severity", "warning"), 1.0)
    confidence = f.get("confidence", 1.0)
    return float(round(weight * confidence / max(effort, 0.1), 3))


def _finding_domain(finding: dict[str, Any]) -> str:
    """Classify a finding into the code or environment bucket."""
    if finding.get("channel") in _ENVIRONMENT_CHANNELS:
        return "environment"
    if finding.get("linter") in _ENVIRONMENT_LINTERS:
        return "environment"
    return "code"


def _summarize_findings(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a code-vs-environment summary for a finding set."""
    domains = {
        "code": {"total": 0, "blocking": 0, "warning": 0, "informational": 0},
        "environment": {"total": 0, "blocking": 0, "warning": 0, "informational": 0},
    }
    channels: dict[str, int] = {}

    for finding in findings:
        domain = _finding_domain(finding)
        domains[domain]["total"] += 1
        severity = str(finding.get("severity", "informational"))
        if severity in domains[domain]:
            domains[domain][severity] += 1
        channel = str(finding.get("channel", ""))
        if channel:
            channels[channel] = channels.get(channel, 0) + 1

    return {
        "domains": domains,
        "channels": channels,
    }


def _extract_findings(
    details,
    channel,
    severity,
    max_issues,
    *,
    finding_domain=None,
    top_n=None,
    time_budget_minutes=None,
):
    """Extract and filter findings from run details.

    When top_n or time_budget_minutes is set, findings are sorted by ROI
    (highest value-per-effort first) and filtered accordingly.
    """
    all_findings = []
    for ch_name, ch_data in _filter_channels(details.get("channels", {}), channel):
        for f in ch_data.get("findings", []):
            if severity and f.get("severity") != severity:
                continue
            all_findings.append({**f, "channel": ch_name})

    if finding_domain and finding_domain != "all":
        all_findings = [f for f in all_findings if _finding_domain(f) == finding_domain]

    # ROI-based sorting when prioritization is requested
    roi_mode = top_n is not None or time_budget_minutes is not None
    if roi_mode:
        for f in all_findings:
            f["roi"] = _finding_roi(f)
        all_findings.sort(key=lambda f: f.get("roi", 0), reverse=True)

    # Time-budget filtering: select findings that fit within the budget
    # Uses _finding_effort() for consistency with ROI ranking (fixable discount)
    if time_budget_minutes is not None:
        budget_findings: list[dict] = []
        remaining = time_budget_minutes
        for f in all_findings:
            effort = _finding_effort(f)
            if remaining >= effort:
                budget_findings.append(f)
                remaining -= effort
        all_findings = budget_findings

    finding_summary = _summarize_findings(all_findings)

    limit = max(top_n, 0) if top_n is not None else max_issues
    result: dict[str, Any] = {
        "total_matching": len(all_findings),
        "findings": all_findings[:limit],
        "finding_summary": finding_summary,
    }
    if len(all_findings) > limit:
        result["truncated"] = len(all_findings) - limit
    if roi_mode:
        result["sorted_by"] = "roi"
        if time_budget_minutes is not None:
            result["time_budget_minutes"] = time_budget_minutes
            result["budget_used_minutes"] = round(
                sum(_finding_effort(f) for f in result["findings"]),
                1,
            )
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
                    "args": {"path": ".", "run_id": run_id, "safe_only": True},
                    "reason": f"Apply {safe_count} safe auto-repairs found in these details.",
                    "priority": 1,
                }
            )

    summary = output.get("finding_summary", {})
    domain_counts = summary.get("domains", {})
    code_total = domain_counts.get("code", {}).get("total", 0)
    environment_total = domain_counts.get("environment", {}).get("total", 0)
    if code_total > 0 and environment_total > 0:
        actions.append(
            {
                "tool": "controlplane_get_details",
                "args": {
                    "run_id": run_id,
                    "finding_domain": "code",
                },
                "reason": "View code findings only without dependency and environment noise.",
                "priority": 4,
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


def _populate_findings_section(
    output: dict,
    details: dict,
    channel,
    severity,
    max_issues,
    **kwargs,
) -> None:
    """Populate findings + delegation annotations into output."""
    output.update(_extract_findings(details, channel, severity, max_issues, **kwargs))
    with contextlib.suppress(Exception):
        from lintgate.controlplane.delegation import annotate_findings_with_suitability

        if "findings" in output:
            annotate_findings_with_suitability(output["findings"], details)


_DEFAULT_SECTIONS = frozenset(
    [
        "findings",
        "channel_details",
        "evidence",
        "repairs",
        "coherence",
        "next_actions",
        "proven_resolutions",
    ]
)


def _populate_coherence(output, details, _channel, _severity, _max_issues, _run_id):
    output["coherence"] = details.get("coherence", {})


def _populate_findings(output, details, channel, severity, max_issues, _run_id, **kwargs):
    _populate_findings_section(output, details, channel, severity, max_issues, **kwargs)


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


def _impl_controlplane_get_details(
    run_id,
    channel,
    severity,
    max_issues,
    sections,
    helpers,
    *,
    finding_domain=None,
    top_n=None,
    time_budget_minutes=None,
):
    """Core implementation of controlplane_get_details."""
    from lintgate.state import load_controlplane_run
    from mcp_tools._disk_helpers import tool_response

    details = load_controlplane_run(run_id)
    if details is None:
        # Fallback: check disk-first analysis files
        import os as _os

        for base in [_os.getcwd(), _os.environ.get("LINTGATE_PROJECT_ROOT", "")]:
            if not base:
                continue
            disk_file = _os.path.join(
                base, ".lintgate", "analysis", "controlplane_run", f"{run_id}.json"
            )
            if _os.path.isfile(disk_file):
                import json as _json

                with open(disk_file, encoding="utf-8") as _f:
                    details = _json.loads(_f.read())
                break
    if details is None:
        raise ValueError(f"No ControlPlane run found with run_id: {run_id}")

    sections_set = set(sections) if sections else _DEFAULT_SECTIONS
    output: dict[str, Any] = {
        "run_id": run_id,
        "duration_ms": details.get("duration_ms", 0),
    }

    extra_kwargs = {}
    if top_n is not None or time_budget_minutes is not None or finding_domain is not None:
        extra_kwargs = {
            "finding_domain": finding_domain,
            "top_n": top_n,
            "time_budget_minutes": time_budget_minutes,
        }

    for section_name, populator in _SECTION_POPULATORS:
        if section_name in sections_set:
            if section_name == "findings" and extra_kwargs:
                populator(output, details, channel, severity, max_issues, run_id, **extra_kwargs)
            else:
                populator(output, details, channel, severity, max_issues, run_id)

    # Build NL summary from output counts by severity
    total = output.get("total_matching", 0)
    sev_label = severity or "all"

    finding_lines = []
    issues = output.get("findings", [])
    if isinstance(issues, list):
        for i, issue in enumerate(issues[:max_issues], 1):
            if isinstance(issue, dict):
                kind = issue.get("kind", "?")
                f = issue.get("file", "?")
                msg = issue.get("message", "")[:60]
                finding_lines.append(f"  {i}. {kind:24s} {f:30s} {msg}")

    lines = [f"{total} {sev_label} findings (showing {min(total, max_issues)}):"]
    lines.extend(finding_lines)

    repairs = output.get("repairs", [])
    if repairs:
        n_repairs = len(repairs) if isinstance(repairs, list) else repairs
        lines.append(f"\nRepairs available: {n_repairs}")

    summary = "\n".join(lines)

    # Derive project_root from environment for disk storage
    project_root = os.environ.get("LINTGATE_PROJECT_ROOT", "") or os.getcwd()

    return tool_response(
        output,
        "controlplane_get_details",
        project_root,
        summary,
        run_id=run_id,
        next_actions=output.get("next_actions"),
    )


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
    from mcp_tools._disk_helpers import tool_response

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

    # Build NL summary
    enabled = status.get("controlplane_enabled", False)
    if enabled:
        channel_count = len([c for c in status.get("channels", {}).values() if c.get("enabled")])
        session_info = status.get("session")
        runs = session_info.get("runs", 0) if session_info else 0
        summary = f"ControlPlane enabled. {channel_count} channels active, {runs} runs in session."
    else:
        summary = "ControlPlane not enabled. Add controlplane.enabled to lintgate.yaml."

    return tool_response(
        status,
        "controlplane_status",
        project_root,
        summary,
    )

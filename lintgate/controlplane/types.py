"""Core data types for the ControlPlane supervision mesh.

These types flow through the entire pipeline:
  SupervisionEvent → Channel.execute() → ChannelResult → CoherenceEngine → MeshResult → Reporter

Design notes:
- Reuses LintIssue from lintgate.types for findings (backward compat)
- ChangeClassification is attached to events to share classification across channels
- RepairAction is opt-in only — channels propose, humans/agents approve
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from lintgate.types import ChangeClassification, LintIssue

# ── Supervision Event ─────────────────────────────────────────────────


@dataclass
class SupervisionEvent:
    """An event that triggers the supervision mesh.

    Created from PostToolUse hook stdin, MCP tool call, or CI trigger.
    Shared across all channels — each channel decides independently
    whether to activate based on the event profile.
    """

    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    surface: Literal["hook", "mcp", "ci"] = "hook"
    project_root: str = ""
    tool_name: str = ""
    files_changed: list[str] = field(default_factory=list)
    change_classification: ChangeClassification | None = None
    raw_input: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    # Opaque artifact bag for pre-pass results (e.g., shared manifest).
    # Populated by run_mesh() Phase 0 and consumed by channels.
    context: dict[str, Any] = field(default_factory=dict)


# ── Repair Actions ────────────────────────────────────────────────────


@dataclass
class RepairAction:
    """A proposed fix from a channel — opt-in apply only.

    Channels propose repairs; the system never auto-applies them.
    The human or agent must explicitly approve via controlplane_apply_repairs().
    """

    action_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    channel: str = ""
    kind: Literal["command", "create_test_skeleton", "config_patch"] = "command"
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    safe: bool = True  # Whether this action is considered safe to auto-approve


# ── Channel Result ────────────────────────────────────────────────────


@dataclass
class ChannelResult:
    """Result from a single channel execution.

    Status semantics:
    - pass: Channel ran, found no issues
    - fail: Channel ran, found issues (severity determines weight)
    - skip: Channel chose not to run (event not relevant)
    - error: Channel crashed (internal error, not a code issue)
    - timeout: Channel exceeded its time budget (partial results may exist)

    Severity semantics:
    - blocking: Agent must address before continuing (lint errors)
    - warning: Agent should address but can continue (test failures in advisory mode)
    - informational: FYI only (missing tests, git hygiene suggestions)
    - none: No findings
    """

    channel: str = ""
    status: Literal["pass", "fail", "skip", "error", "timeout"] = "pass"
    severity: Literal["blocking", "warning", "informational", "none"] = "none"
    findings: list[LintIssue] = field(default_factory=list)
    repairs: list[RepairAction] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    error_message: str | None = None


# ── Coherence Result ──────────────────────────────────────────────────


@dataclass
class CoherenceResult:
    """Cross-channel coherence diagnosis.

    States (from the ControlPlane plan):
    - stable: All enabled channels pass or skip
    - isolated: Exactly one channel fails. Highest confidence when >=2 others
      pass (Monty Hall: silence in other channels concentrates attention on the
      failing one)
    - coupled: Two+ channels fail with overlapping files
    - systemic: Three+ channels fail, or cross-domain failure pattern
    - degraded: Any channel error/timeout beyond threshold

    Confidence (0.0-1.0) indicates how certain the classification is:
    - 1.0: Unambiguous (e.g., stable with all pass, degraded with errors)
    - 0.8+: High confidence (e.g., isolated with 3+ silent channels)
    - 0.5-0.8: Moderate (e.g., coupled vs systemic boundary cases)
    - <0.5: Low confidence (e.g., 1 fail + 0 pass, ambiguous coupled/systemic)

    classification_notes lists factors that made classification uncertain,
    making brittle edge cases transparent rather than silently misclassified.
    """

    state: Literal["stable", "isolated", "coupled", "systemic", "degraded"] = "stable"
    summary: str = ""
    recommended_action: str = ""
    classification_reason: str = ""  # Why this state was chosen
    silent_channels: list[str] = field(default_factory=list)
    loud_channels: list[str] = field(default_factory=list)
    confidence: float = 1.0
    classification_notes: list[str] = field(default_factory=list)
    # Edit-scope classification (Phase 2)
    edit_scoped: bool = False
    edit_related_channels: list[str] = field(default_factory=list)
    ambient_channels: list[str] = field(default_factory=list)
    unknown_scope_channels: list[str] = field(default_factory=list)


# ── Mesh Result ───────────────────────────────────────────────────────


@dataclass
class MeshResult:
    """Complete result from the supervision mesh.

    This is the final output of run_mesh() — consumed by the reporter
    to produce systemMessage + hookSpecificOutput.
    """

    event: SupervisionEvent = field(default_factory=SupervisionEvent)
    channel_results: list[ChannelResult] = field(default_factory=list)
    coherence: CoherenceResult = field(default_factory=CoherenceResult)
    duration_ms: float = 0.0
    incomplete_channels: list[str] = field(default_factory=list)
    partial: bool = False  # True if any channel was shed due to timeout
    # Git-aware scope signaling (#179): working tree context for scope annotation
    git_context: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionTransferPacket:
    """A portable packet summarizing a session for handoff."""

    source_agent_id: str = ""
    target_agent_id: str = ""
    transfer_reason: str = ""
    active_findings: list[dict[str, Any]] = field(default_factory=list)
    resolved_findings: list[dict[str, Any]] = field(default_factory=list)
    context_summary: str = ""
    timestamp: float = field(default_factory=time.time)


# ── Config Types ──────────────────────────────────────────────────────


@dataclass
class ChannelConfig:
    """Per-channel configuration."""

    enabled: bool = True
    blocking: bool = False  # Can this channel's findings block the agent?
    timeout_ms: int = 8000
    max_findings_shown: int = 5
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenPolicy:
    """Token budget policy for the reporter."""

    hook_max_tokens: int = 900
    include_pass_details: bool = False  # OTP-inspired: silent channels omitted


@dataclass
class InquiryConfig:
    """Configuration for the Architecture of Inquiry features.

    Groups all inquiry-related flags under controlplane.inquiry.*
    to avoid config sprawl at the top level.
    """

    theory_grounded_signals: bool = False
    prediction_tracking: bool = False
    theory_coherence_check: bool = False
    living_context: bool = False
    session_gate: bool = False

    def any_enabled(self) -> bool:
        """Check if any inquiry feature is enabled."""
        return any(
            [
                self.theory_grounded_signals,
                self.prediction_tracking,
                self.theory_coherence_check,
                self.living_context,
                self.session_gate,
            ]
        )


@dataclass
class QualityGateConfig:
    """Configuration for the quality gate in PreToolUse hooks.

    Controls whether git push is blocked and git commit is advised
    when quality checks haven't passed.
    """

    enabled: bool = False
    staleness_threshold_s: float = 1800.0  # 30 min
    block_push: bool = True
    advise_commit: bool = True
    check_secrets: bool = True


@dataclass
class DispositionEnforcementConfig:
    """Configuration for disposition enforcement in hooks."""

    enabled: bool = True
    max_ignores_before_blocking: int = 3
    enforce_on_channels: list[str] = field(default_factory=lambda: ["behavior", "lint"])
    nudge_after_edit_without_lint: bool = True
    nudge_before_bash_without_prediction: bool = True
    cadence_health_check_events: int = 15
    max_nudges_per_disposition: int = 3


@dataclass
class ControlPlaneConfig:
    """Top-level ControlPlane configuration.

    Parsed from the 'controlplane:' section of lintgate.yaml.
    When enabled=False (default), the entire ControlPlane is bypassed
    and LintGate behaves exactly as before.
    """

    enabled: bool = False
    latency_budget_ms: int = 120_000  # 2 minutes; MCP/CLI may override dynamically
    advisory_default: bool = True
    channels: dict[str, ChannelConfig] = field(default_factory=dict)
    token_policy: TokenPolicy = field(default_factory=TokenPolicy)
    session_memory: bool = False
    session_max_age_hours: float = 4.0
    constraint_proposal_threshold: int = 5
    # Architecture of Inquiry features (grouped under controlplane.inquiry.*)
    inquiry: InquiryConfig = field(default_factory=InquiryConfig)
    # Severity-weighted coherence: when True, informational-only channel failures
    # count less toward the "systemic" threshold (0.25 vs 1.0 for blocking).
    severity_weighted_coherence: bool = True
    # Per-channel importance weights for coherence classification.
    # None = disabled (all channels equal weight, current behavior).
    # Example: {"structure": 0.4, "behavior": 0.3} — unspecified channels get 0.5.
    coherence_channel_weights: dict[str, float] | None = None
    # Global behavior profile (cross-session learning)
    global_memory_enabled: bool = False
    global_memory_alpha: float = 0.6
    global_memory_decay_horizon: int = 50
    global_memory_ttl_days: int = 90
    # Compass system (cognitive mode axis tracking)
    compass_enabled: bool = False
    compass_staleness_hours: float = 24.0
    # Quality gate (PreToolUse hook enforcement)
    quality_gate: QualityGateConfig = field(default_factory=QualityGateConfig)
    # Habit Mode (context window management)
    habit_mode_enabled: bool = True
    habit_mode_auto_detect: bool = True
    habit_mode_compact_threshold: float = 0.40
    habit_mode_token_api_interval: int = 15
    habit_mode_enter_score: float = 0.70
    habit_mode_exit_score: float = 0.40
    habit_mode_sustain_calls: int = 5
    # Message arbitration (hook output control)
    hook_verbosity: str = "full"  # "silent" | "pulse" | "full" | "auto"
    hook_pulse_interval: int = 5  # Events between pulse emissions
    hook_dispositions_enabled: bool = True  # Enable disposition injection
    disposition_enforcement: DispositionEnforcementConfig = field(
        default_factory=DispositionEnforcementConfig
    )

    def channel_enabled(self, name: str) -> bool:
        """Check if a specific channel is enabled."""
        if name in self.channels:
            return self.channels[name].enabled
        return True  # Channels enabled by default

    def channel_blocking(self, name: str) -> bool:
        """Check if a channel can produce blocking findings."""
        if name in self.channels:
            return self.channels[name].blocking
        # Default: only lint is blocking
        return name == "lint"

    def channel_timeout(self, name: str) -> int:
        """Get per-channel timeout in ms."""
        if name in self.channels:
            return self.channels[name].timeout_ms
        return 8000

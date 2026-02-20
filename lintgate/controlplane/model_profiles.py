"""Model-specific behavioral profiles — calibration layer.

Stores per-model risk vectors and probe results. Each LLM model has different
behavioral tendencies (approach cycling, verification debt, premature action).
This module captures those tendencies through a deterministic probe and
passive telemetry, then provides them to the bootstrap pipeline for
model-aware content generation.

Storage location:
  1. LINTGATE_HOME env var (if set) / model_profiles.json
  2. ~/.lintgate/model_profiles.json (default)

NOT in ~/.claude/lintgate/ — model profiles are model-agnostic infrastructure.

Non-negotiable: No raw prompt/response transcripts stored. Only selected
option letters and derived scores.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ── Constants ────────────────────────────────────────────────────────

DEFAULT_STALENESS_DAYS = 30
DEFAULT_MIN_CONFIDENCE = 0.55
PROFILE_FORMAT_VERSION = 1

# EMA constants for telemetry refinement
_EMA_ALPHA = 0.15  # probe anchors, telemetry nudges


def _lintgate_home() -> Path:
    """Resolve LINTGATE_HOME with fallback to ~/.lintgate."""
    env = os.environ.get("LINTGATE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".lintgate"


# ── Model Key Resolution ────────────────────────────────────────────

_PROVIDER_PREFIXES: dict[str, str] = {
    "claude": "anthropic",
    "gpt": "openai",
    "o1": "openai",
    "o3": "openai",
    "o4": "openai",
    "gemini": "google",
    "llama": "meta",
    "mistral": "mistralai",
    "codestral": "mistralai",
    "command": "cohere",
    "deepseek": "deepseek",
}


def resolve_model_key(raw_model_id: str | None) -> str | None:
    """Normalize model identifier to provider:model canonical form.

    Returns None if input is None, empty, or unresolvable.
    None means the system should fall back to generic defaults.

    Examples:
        "claude-opus-4" -> "anthropic:claude-opus-4"
        "gpt-4o" -> "openai:gpt-4o"
        "gemini-2.0-flash" -> "google:gemini-2.0-flash"
        "anthropic:claude-opus-4" -> "anthropic:claude-opus-4"
        "" -> None
        None -> None
        "some-unknown-thing" -> None
    """
    if not raw_model_id:
        return None

    raw = raw_model_id.strip().lower()
    if not raw:
        return None

    # Already in provider:model format
    if ":" in raw:
        return raw

    # Infer provider from prefix
    for prefix, provider in _PROVIDER_PREFIXES.items():
        if raw.startswith(prefix):
            return f"{provider}:{raw}"

    # Unknown model — return None to trigger graceful fallback
    return None


# ── Data Model ───────────────────────────────────────────────────────


@dataclass
class ModelProfile:
    """Behavioral profile for a specific LLM model.

    Populated by the deterministic quick probe (model_probe.py).
    Used to bias bootstrap defaults and behavioral thresholds.
    """

    model_key: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    probe_version: int = 1
    probe_runs: int = 0
    confidence: float = 0.0
    signal_risk: dict[str, float] = field(default_factory=dict)
    custom_anti_patterns: list[str] = field(default_factory=list)
    custom_dispositions: list[str] = field(default_factory=list)
    telemetry_samples: int = 0  # Cumulative updates across all sessions
    stale_after_days: int = DEFAULT_STALENESS_DAYS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelProfile:
        return cls(
            model_key=data.get("model_key", ""),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            last_seen_at=data.get("last_seen_at", time.time()),
            probe_version=data.get("probe_version", 1),
            probe_runs=data.get("probe_runs", 0),
            confidence=data.get("confidence", 0.0),
            signal_risk=data.get("signal_risk", {}),
            custom_anti_patterns=data.get("custom_anti_patterns", []),
            custom_dispositions=data.get("custom_dispositions", []),
            telemetry_samples=data.get("telemetry_samples", 0),
            stale_after_days=data.get("stale_after_days", DEFAULT_STALENESS_DAYS),
        )

    def is_stale(self) -> bool:
        """Check if profile is older than staleness threshold."""
        age_days = (time.time() - self.updated_at) / 86400
        return age_days > self.stale_after_days

    def is_usable(self, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> bool:
        """Check if profile has sufficient confidence for use."""
        return self.confidence >= min_confidence and not self.is_stale()


# ── Store ────────────────────────────────────────────────────────────


@dataclass
class ModelProfileStore:
    """Container for all model profiles, serialized as one JSON file."""

    format_version: int = PROFILE_FORMAT_VERSION
    profiles: dict[str, ModelProfile] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "profiles": {k: v.to_dict() for k, v in self.profiles.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelProfileStore:
        store = cls(format_version=data.get("format_version", 1))
        for key, pdata in data.get("profiles", {}).items():
            if isinstance(pdata, dict):
                store.profiles[key] = ModelProfile.from_dict(pdata)
        return store


# ── Persistence ──────────────────────────────────────────────────────


def load_profiles() -> ModelProfileStore:
    """Load all model profiles from disk. Returns empty store on error."""
    path = _lintgate_home() / "model_profiles.json"
    if not path.exists():
        return ModelProfileStore()
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return ModelProfileStore()
        return ModelProfileStore.from_dict(data)
    except (json.JSONDecodeError, OSError):
        return ModelProfileStore()


def save_profiles(store: ModelProfileStore) -> None:
    """Save all model profiles to disk. Non-fatal on error."""
    path = _lintgate_home() / "model_profiles.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "w") as f:
            json.dump(store.to_dict(), f, indent=2)
    except OSError:
        pass  # Non-fatal — profiles are observability, not correctness


def get_profile(model_key: str) -> ModelProfile | None:
    """Get a specific model's profile, or None if not found."""
    store = load_profiles()
    canonical = resolve_model_key(model_key)
    if canonical is None:
        return None
    return store.profiles.get(canonical)


def upsert_profile(profile: ModelProfile) -> None:
    """Save/update a single model profile."""
    store = load_profiles()
    store.profiles[profile.model_key] = profile
    save_profiles(store)


def reset_profile(model_key: str) -> bool:
    """Remove a model profile. Returns True if found and removed."""
    canonical = resolve_model_key(model_key)
    if canonical is None:
        return False
    store = load_profiles()
    if canonical in store.profiles:
        del store.profiles[canonical]
        save_profiles(store)
        return True
    return False


# ── Telemetry Refinement ─────────────────────────────────────────────


def apply_telemetry_update(
    profile: ModelProfile,
    observed_signal_fires: dict[str, int],
    event_count: int,
) -> None:
    """Update model profile signal_risk via EMA from observed behavior.

    The probe result is the anchor. Telemetry nudges the profile
    toward observed reality using exponential moving average.

    Args:
        profile: The model profile to update (modified in place).
        observed_signal_fires: {signal_name: fire_count} from this session.
        event_count: Total events in the session (for normalization).

    Note:
        Session-scoped rate limiting is enforced by the caller (hook layer),
        not here. This function applies one update whenever invoked.
    """
    if event_count < 10:
        return  # Too few events to be meaningful

    updated_any = False
    for signal, fires in observed_signal_fires.items():
        if fires <= 0:
            continue

        # Normalize observed fires to [0, 1] range
        observed_risk = min(1.0, fires / max(event_count, 1) * 10)

        current_risk = profile.signal_risk.get(signal, 0.0)

        # EMA: new = alpha * observed + (1-alpha) * current
        updated = _EMA_ALPHA * observed_risk + (1 - _EMA_ALPHA) * current_risk
        profile.signal_risk[signal] = max(0.0, min(1.0, round(updated, 4)))
        updated_any = True

    if updated_any:
        profile.telemetry_samples += 1
        profile.last_seen_at = time.time()
        profile.updated_at = time.time()

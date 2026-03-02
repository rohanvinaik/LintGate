"""Project configuration loading.

Loads per-project .claude/lintgate.yaml with fallback to auto-detection.
Auto-detection scans for pyproject.toml, tsconfig.json, Cargo.toml etc.
and probes for installed tools (ruff, mypy, radon, bandit).

Zero config required — works out of the box for any Python project
with ruff installed.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from .types import (
    CoveragePolicy,
    ProjectConfig,
    QualityPolicy,
    SecurityPolicy,
    ToleratedFalsePositive,
)

if TYPE_CHECKING:
    from .controlplane.types import ControlPlaneConfig

try:
    import yaml  # type: ignore[import-untyped]

    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False


def load_controlplane_config(cwd: str) -> ControlPlaneConfig | None:
    """Load ControlPlane configuration from lintgate.yaml.

    Returns None if the controlplane section is absent or disabled.
    The caller can check `config.enabled` to decide whether to use it.
    """
    from .controlplane.types import (
        ChannelConfig,
        ControlPlaneConfig,
        DispositionEnforcementConfig,
        InquiryConfig,
        QualityGateConfig,
        TokenPolicy,
    )

    config_path = os.path.join(cwd, ".claude", "lintgate.yaml")
    if not os.path.exists(config_path) or not _YAML_AVAILABLE:
        return None

    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
    except Exception:
        return None

    cp_raw = raw.get("controlplane")
    if not cp_raw or not isinstance(cp_raw, dict):
        return None

    cp_config = ControlPlaneConfig(
        enabled=cp_raw.get("enabled", False),
        latency_budget_ms=cp_raw.get("latency_budget_ms", 15000),
        advisory_default=cp_raw.get("advisory_default", True),
        session_memory=cp_raw.get("session_memory", False),
        session_max_age_hours=float(cp_raw.get("session_max_age_hours", 4.0)),
        constraint_proposal_threshold=int(
            cp_raw.get("constraint_proposal_threshold", 5)
        ),
        severity_weighted_coherence=bool(
            cp_raw.get("severity_weighted_coherence", False)
        ),
    )

    # Parse coherence channel weights (None = disabled)
    coherence_raw = cp_raw.get("coherence", {})
    if isinstance(coherence_raw, dict):
        weights = coherence_raw.get("channel_weights")
        if isinstance(weights, dict):
            cp_config.coherence_channel_weights = {
                str(k): float(v) for k, v in weights.items()
            }

    # Parse inquiry config (Architecture of Inquiry features)
    inquiry_raw = cp_raw.get("inquiry", {})
    if isinstance(inquiry_raw, dict):
        cp_config.inquiry = InquiryConfig(
            theory_grounded_signals=bool(
                inquiry_raw.get("theory_grounded_signals", False)
            ),
            prediction_tracking=bool(inquiry_raw.get("prediction_tracking", False)),
            theory_coherence_check=bool(
                inquiry_raw.get("theory_coherence_check", False)
            ),
            living_context=bool(inquiry_raw.get("living_context", False)),
            session_gate=bool(inquiry_raw.get("session_gate", False)),
        )

    # Parse global memory config
    global_mem = cp_raw.get("global_memory", {})
    if isinstance(global_mem, dict):
        cp_config.global_memory_enabled = global_mem.get("enabled", False)
        cp_config.global_memory_alpha = float(global_mem.get("alpha", 0.6))
        cp_config.global_memory_decay_horizon = int(global_mem.get("decay_horizon", 50))
        cp_config.global_memory_ttl_days = int(global_mem.get("ttl_days", 90))

    # Parse token policy
    token_raw = cp_raw.get("token_policy", {})
    if isinstance(token_raw, dict):
        cp_config.token_policy = TokenPolicy(
            hook_max_tokens=token_raw.get("hook_max_tokens", 900),
            include_pass_details=token_raw.get("include_pass_details", False),
        )

    # Parse habit_mode config
    habit_raw = cp_raw.get("habit_mode", {})
    if isinstance(habit_raw, dict):
        cp_config.habit_mode_enabled = habit_raw.get("enabled", True)
        cp_config.habit_mode_auto_detect = habit_raw.get("auto_detect", True)
        cp_config.habit_mode_compact_threshold = float(
            habit_raw.get("compact_threshold", 0.25)
        )
        cp_config.habit_mode_token_api_interval = int(
            habit_raw.get("token_api_interval", 15)
        )
        cp_config.habit_mode_enter_score = float(habit_raw.get("enter_score", 0.70))
        cp_config.habit_mode_exit_score = float(habit_raw.get("exit_score", 0.40))
        cp_config.habit_mode_sustain_calls = int(habit_raw.get("sustain_calls", 5))

    # Parse message arbitration config
    cp_config.hook_verbosity = str(cp_raw.get("hook_verbosity", "full"))
    cp_config.hook_pulse_interval = int(cp_raw.get("hook_pulse_interval", 5))
    cp_config.hook_dispositions_enabled = bool(
        cp_raw.get("hook_dispositions_enabled", True)
    )

    # Parse compass config
    compass_raw = cp_raw.get("compass", {})
    if isinstance(compass_raw, dict):
        cp_config.compass_enabled = compass_raw.get("enabled", False)
        cp_config.compass_staleness_hours = float(
            compass_raw.get("staleness_hours", 24.0)
        )

    # Parse quality_gate config
    qg_raw = cp_raw.get("quality_gate", {})
    if isinstance(qg_raw, dict):
        cp_config.quality_gate = QualityGateConfig(
            enabled=bool(qg_raw.get("enabled", False)),
            staleness_threshold_s=float(qg_raw.get("staleness_threshold_s", 1800.0)),
            block_push=bool(qg_raw.get("block_push", True)),
            advise_commit=bool(qg_raw.get("advise_commit", True)),
            check_secrets=bool(qg_raw.get("check_secrets", True)),
        )

    # Parse disposition enforcement config
    disp_raw = cp_raw.get("disposition_enforcement", {})
    if isinstance(disp_raw, dict):
        cp_config.disposition_enforcement = DispositionEnforcementConfig(
            enabled=bool(disp_raw.get("enabled", True)),
            max_ignores_before_blocking=int(
                disp_raw.get("max_ignores_before_blocking", 3)
            ),
            enforce_on_channels=disp_raw.get(
                "enforce_on_channels", ["behavior", "lint"]
            ),
        )

    # Parse per-channel configs
    channels_raw = cp_raw.get("channels", {})
    if isinstance(channels_raw, dict):
        for name, ch_conf in channels_raw.items():
            if isinstance(ch_conf, dict):
                known_keys = {"enabled", "blocking", "timeout_ms", "max_findings_shown"}
                settings = {k: v for k, v in ch_conf.items() if k not in known_keys}
                cp_config.channels[name] = ChannelConfig(
                    enabled=ch_conf.get("enabled", True),
                    blocking=ch_conf.get("blocking", name == "lint"),
                    timeout_ms=ch_conf.get("timeout_ms", 8000),
                    max_findings_shown=ch_conf.get("max_findings_shown", 5),
                    settings=settings,
                )
            elif isinstance(ch_conf, bool):
                cp_config.channels[name] = ChannelConfig(enabled=ch_conf)

    return cp_config


def load_config(cwd: str) -> ProjectConfig:
    """Load project config with fallback to auto-detection.

    Priority:
    1. <project>/.claude/lintgate.yaml (explicit config)
    2. Auto-detect from project structure
    """
    # Try project-level config
    config_path = os.path.join(cwd, ".claude", "lintgate.yaml")
    if os.path.exists(config_path) and _YAML_AVAILABLE:
        return _load_yaml_config(config_path, cwd)

    # Auto-detect
    return _auto_detect(cwd)


def _load_yaml_config(config_path: str, cwd: str) -> ProjectConfig:
    """Load config from a YAML file."""
    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    config = ProjectConfig(project_root=cwd)

    # Linters
    linters_raw = raw.get("linters", {})
    for name, linter_conf in linters_raw.items():
        if isinstance(linter_conf, dict):
            config.enabled_linters[name] = linter_conf.get("enabled", True)
            config.linter_configs[name] = linter_conf
        elif isinstance(linter_conf, bool):
            config.enabled_linters[name] = linter_conf

    # Pipeline-critical paths
    config.pipeline_critical_paths = raw.get("pipeline_critical_paths", [])

    # Severity overrides
    config.severity_overrides = raw.get("severity_overrides", {})

    # Exemptions
    config.exemptions = raw.get("exemptions", {})

    # Extra tier 3 linters
    config.extra_tier3_linters = raw.get("extra_tier3_linters", [])

    # Tool version constraints (for version auditing/checking)
    tool_versions_raw = raw.get("tool_versions", {})
    if isinstance(tool_versions_raw, dict):
        config.tool_version_requirements = {
            str(k): str(v)
            for k, v in tool_versions_raw.items()
            if isinstance(k, str) and isinstance(v, (str, int, float))
        }

    # Version checker specific settings
    vcheck_raw = linters_raw.get("version_checker", {})
    if isinstance(vcheck_raw, dict):
        config.enforced_optional_groups = vcheck_raw.get("enforced_optional_groups", [])

    # Path policies (per-path tier/strictness overrides)
    path_policies_raw = raw.get("path_policies", [])
    if isinstance(path_policies_raw, list):
        for policy in path_policies_raw:
            if isinstance(policy, dict) and "glob" in policy:
                config.path_policies.append(
                    {
                        "glob": policy["glob"],
                        "tier": int(policy.get("tier", 2)),
                        "strictness": policy.get("strictness", "normal"),
                        "include_info": policy.get("include_info", True),
                    }
                )

    # Debounce
    debounce_raw = raw.get("debounce", {})
    if debounce_raw:
        for key in ("tier_0", "tier_1", "tier_2", "tier_3"):
            interval_key = f"{key}_interval_s"
            if interval_key in debounce_raw:
                config.debounce[key] = float(debounce_raw[interval_key])

    # Timeout
    config.total_timeout_ms = raw.get("total_timeout_ms", 8000)

    # Discovery scope
    discovery_raw = raw.get("discovery", {})
    if isinstance(discovery_raw, dict):
        sp = discovery_raw.get("source_paths", [])
        if isinstance(sp, list):
            config.discovery_source_paths = [str(p) for p in sp if p]
        ep = discovery_raw.get("exclude_paths", [])
        if isinstance(ep, list):
            config.discovery_exclude_paths = [str(p) for p in ep if p]

    # Quality policy
    qp_raw = raw.get("quality_policy", {})
    if isinstance(qp_raw, dict):
        config.quality_policy = _parse_quality_policy(qp_raw)

    # Languages
    config.languages = raw.get("languages", [])
    if not config.languages:
        config.languages = _detect_languages(cwd)

    return config


def _auto_detect(cwd: str) -> ProjectConfig:
    """Auto-detect project type and configure linters.

    Probes for:
    - Language markers (pyproject.toml, tsconfig.json, Cargo.toml)
    - Installed tools (ruff, mypy, radon, bandit)
    """
    config = ProjectConfig(project_root=cwd)
    config.languages = _detect_languages(cwd)

    # Auto-enable linters based on what's installed
    if "python" in config.languages:
        if shutil.which("ruff"):
            config.enabled_linters["ruff_check"] = True
            config.enabled_linters["ruff_format"] = True
        if shutil.which("mypy"):
            config.enabled_linters["mypy"] = True
        if shutil.which("radon"):
            config.enabled_linters["complexity_checker"] = True
        if shutil.which("bandit"):
            config.enabled_linters["bandit"] = True

    return config


def _parse_quality_policy(raw: dict) -> QualityPolicy:
    """Parse quality_policy section from lintgate.yaml."""
    policy = QualityPolicy()

    cov_raw = raw.get("coverage", {})
    if isinstance(cov_raw, dict):
        source_packages_raw = cov_raw.get("source_packages", ["lintgate", "mcp_tools"])
        source_packages = ["lintgate", "mcp_tools"]
        if isinstance(source_packages_raw, list):
            cleaned = [
                str(pkg).strip() for pkg in source_packages_raw if str(pkg).strip()
            ]
            if cleaned:
                source_packages = cleaned
        elif isinstance(source_packages_raw, str) and source_packages_raw.strip():
            source_packages = [source_packages_raw.strip()]

        global_threshold = _coerce_int(cov_raw.get("global_threshold", 80), default=80)
        diff_threshold = _coerce_int(cov_raw.get("diff_threshold", 80), default=80)
        policy.coverage = CoveragePolicy(
            global_threshold=max(0, min(100, global_threshold)),
            diff_threshold=max(0, min(100, diff_threshold)),
            source_packages=source_packages,
        )

    sec_raw = raw.get("security", {})
    if isinstance(sec_raw, dict):
        fps: list[ToleratedFalsePositive] = []
        for fp_entry in sec_raw.get("tolerated_false_positives", []):
            if isinstance(fp_entry, dict):
                fps.append(
                    ToleratedFalsePositive(
                        rule=str(fp_entry.get("rule", "")),
                        file=str(fp_entry.get("file", "")),
                        scope=str(fp_entry.get("scope", "")),
                        reason=str(fp_entry.get("reason", "")),
                    )
                )
        policy.security = SecurityPolicy(tolerated_false_positives=fps)

    return policy


def _coerce_int(value: object, default: int) -> int:
    """Best-effort int coercion used for config parsing."""
    try:
        return int(value)  # type: ignore[arg-type,call-overload,no-any-return]
    except (TypeError, ValueError):
        return default


def _detect_languages(cwd: str) -> list[str]:
    """Detect which programming languages are in this project."""
    languages = []
    root = Path(cwd)

    # Python
    python_markers = [
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "Pipfile",
    ]
    if any((root / m).exists() for m in python_markers) or list(root.glob("*.py")):
        languages.append("python")

    # TypeScript / JavaScript
    if (root / "tsconfig.json").exists() or (root / "package.json").exists():
        languages.append("typescript")

    # Rust
    if (root / "Cargo.toml").exists():
        languages.append("rust")

    # Go
    if (root / "go.mod").exists():
        languages.append("go")

    # Swift
    if (root / "Package.swift").exists():
        languages.append("swift")

    return languages

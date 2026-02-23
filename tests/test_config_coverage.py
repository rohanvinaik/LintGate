"""Tests for lintgate/config.py — full coverage of all public and private functions."""

from __future__ import annotations

import textwrap

from lintgate.config import (
    _auto_detect,
    _coerce_int,
    _detect_languages,
    _load_yaml_config,
    _parse_quality_policy,
    load_config,
    load_controlplane_config,
)
from lintgate.types import QualityPolicy

# ── _coerce_int ──────────────────────────────────────────────────────────


class TestCoerceInt:
    def test_valid_int(self) -> None:
        assert _coerce_int(42, default=0) == 42

    def test_valid_string_int(self) -> None:
        assert _coerce_int("99", default=0) == 99

    def test_float_truncates(self) -> None:
        assert _coerce_int(3.9, default=0) == 3

    def test_invalid_string_returns_default(self) -> None:
        assert _coerce_int("abc", default=7) == 7

    def test_none_returns_default(self) -> None:
        assert _coerce_int(None, default=5) == 5

    def test_empty_string_returns_default(self) -> None:
        assert _coerce_int("", default=10) == 10


# ── _detect_languages ───────────────────────────────────────────────────


class TestDetectLanguages:
    def test_python_pyproject(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        (p / "pyproject.toml").write_text("[tool.pytest]")
        assert "python" in _detect_languages(str(p))

    def test_python_setup_py(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        (p / "setup.py").write_text("from setuptools import setup")
        assert "python" in _detect_languages(str(p))

    def test_python_requirements(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        (p / "requirements.txt").write_text("flask==2.0")
        assert "python" in _detect_languages(str(p))

    def test_python_py_file(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        (p / "main.py").write_text("print('hello')")
        assert "python" in _detect_languages(str(p))

    def test_typescript(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        (p / "tsconfig.json").write_text("{}")
        langs = _detect_languages(str(p))
        assert "typescript" in langs

    def test_javascript_package_json(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        (p / "package.json").write_text("{}")
        assert "typescript" in _detect_languages(str(p))

    def test_rust(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        (p / "Cargo.toml").write_text("[package]")
        assert "rust" in _detect_languages(str(p))

    def test_go(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        (p / "go.mod").write_text("module example.com/m")
        assert "go" in _detect_languages(str(p))

    def test_swift(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        (p / "Package.swift").write_text("// swift-tools-version:5.0")
        assert "swift" in _detect_languages(str(p))

    def test_empty_dir(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        assert _detect_languages(str(p)) == []

    def test_multiple_languages(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        (p / "pyproject.toml").write_text("[tool]")
        (p / "Cargo.toml").write_text("[package]")
        langs = _detect_languages(str(p))
        assert "python" in langs
        assert "rust" in langs


# ── _parse_quality_policy ────────────────────────────────────────────────


class TestParseQualityPolicy:
    def test_empty_dict(self) -> None:
        policy = _parse_quality_policy({})
        assert isinstance(policy, QualityPolicy)
        assert policy.coverage.global_threshold == 80
        assert policy.coverage.diff_threshold == 80

    def test_coverage_thresholds(self) -> None:
        raw = {"coverage": {"global_threshold": 90, "diff_threshold": 70}}
        policy = _parse_quality_policy(raw)
        assert policy.coverage.global_threshold == 90
        assert policy.coverage.diff_threshold == 70

    def test_coverage_clamp_above_100(self) -> None:
        raw = {"coverage": {"global_threshold": 150}}
        policy = _parse_quality_policy(raw)
        assert policy.coverage.global_threshold == 100

    def test_coverage_clamp_below_0(self) -> None:
        raw = {"coverage": {"global_threshold": -10}}
        policy = _parse_quality_policy(raw)
        assert policy.coverage.global_threshold == 0

    def test_source_packages_list(self) -> None:
        raw = {"coverage": {"source_packages": ["mylib", "utils"]}}
        policy = _parse_quality_policy(raw)
        assert policy.coverage.source_packages == ["mylib", "utils"]

    def test_source_packages_string(self) -> None:
        raw = {"coverage": {"source_packages": "single_pkg"}}
        policy = _parse_quality_policy(raw)
        assert policy.coverage.source_packages == ["single_pkg"]

    def test_source_packages_empty_list_uses_default(self) -> None:
        raw = {"coverage": {"source_packages": []}}
        policy = _parse_quality_policy(raw)
        assert policy.coverage.source_packages == ["lintgate", "mcp_tools"]

    def test_source_packages_with_whitespace(self) -> None:
        raw = {"coverage": {"source_packages": [" pkg1 ", "  ", " pkg2"]}}
        policy = _parse_quality_policy(raw)
        assert policy.coverage.source_packages == ["pkg1", "pkg2"]

    def test_security_false_positives(self) -> None:
        raw = {
            "security": {
                "tolerated_false_positives": [
                    {"rule": "S2083", "file": "foo.py", "scope": "*.py", "reason": "safe"},
                ]
            }
        }
        policy = _parse_quality_policy(raw)
        assert len(policy.security.tolerated_false_positives) == 1
        fp = policy.security.tolerated_false_positives[0]
        assert fp.rule == "S2083"
        assert fp.reason == "safe"

    def test_security_ignores_non_dict_entries(self) -> None:
        raw = {"security": {"tolerated_false_positives": ["not_a_dict", 42]}}
        policy = _parse_quality_policy(raw)
        assert len(policy.security.tolerated_false_positives) == 0


# ── _load_yaml_config ────────────────────────────────────────────────────


class TestLoadYamlConfig:
    def _write_config(self, tmp_path: object, content: str) -> str:
        p = tmp_path  # type: ignore[assignment]
        claude_dir = p / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        config_path = claude_dir / "lintgate.yaml"
        config_path.write_text(content)
        return str(config_path)

    def test_basic_linters(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        config_path = self._write_config(
            p,
            textwrap.dedent("""\
                linters:
                  ruff_check:
                    enabled: true
                  mypy:
                    enabled: false
            """),
        )
        (p / "pyproject.toml").write_text("")  # ensure python detected
        config = _load_yaml_config(config_path, str(p))
        assert config.enabled_linters["ruff_check"] is True
        assert config.enabled_linters["mypy"] is False

    def test_linter_bool_shorthand(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        config_path = self._write_config(
            p,
            textwrap.dedent("""\
                linters:
                  ruff_check: true
                  bandit: false
            """),
        )
        config = _load_yaml_config(config_path, str(p))
        assert config.enabled_linters["ruff_check"] is True
        assert config.enabled_linters["bandit"] is False

    def test_pipeline_critical_paths(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        config_path = self._write_config(
            p,
            textwrap.dedent("""\
                pipeline_critical_paths:
                  - src/core.py
                  - src/api.py
            """),
        )
        config = _load_yaml_config(config_path, str(p))
        assert config.pipeline_critical_paths == ["src/core.py", "src/api.py"]

    def test_severity_overrides(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        config_path = self._write_config(
            p,
            textwrap.dedent("""\
                severity_overrides:
                  E501: informational
            """),
        )
        config = _load_yaml_config(config_path, str(p))
        assert config.severity_overrides == {"E501": "informational"}

    def test_tool_versions(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        config_path = self._write_config(
            p,
            textwrap.dedent("""\
                tool_versions:
                  ruff: "0.4.0"
                  mypy: "1.10"
            """),
        )
        config = _load_yaml_config(config_path, str(p))
        assert config.tool_version_requirements == {"ruff": "0.4.0", "mypy": "1.10"}

    def test_path_policies(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        config_path = self._write_config(
            p,
            textwrap.dedent("""\
                path_policies:
                  - glob: "tests/**"
                    tier: 1
                    strictness: relaxed
                    include_info: false
            """),
        )
        config = _load_yaml_config(config_path, str(p))
        assert len(config.path_policies) == 1
        assert config.path_policies[0]["glob"] == "tests/**"
        assert config.path_policies[0]["tier"] == 1
        assert config.path_policies[0]["strictness"] == "relaxed"

    def test_debounce(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        config_path = self._write_config(
            p,
            textwrap.dedent("""\
                debounce:
                  tier_0_interval_s: 1.5
                  tier_2_interval_s: 4.0
            """),
        )
        config = _load_yaml_config(config_path, str(p))
        assert config.debounce["tier_0"] == 1.5
        assert config.debounce["tier_2"] == 4.0

    def test_total_timeout(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        config_path = self._write_config(
            p,
            textwrap.dedent("""\
                total_timeout_ms: 12000
            """),
        )
        config = _load_yaml_config(config_path, str(p))
        assert config.total_timeout_ms == 12000

    def test_quality_policy_section(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        config_path = self._write_config(
            p,
            textwrap.dedent("""\
                quality_policy:
                  coverage:
                    global_threshold: 95
            """),
        )
        config = _load_yaml_config(config_path, str(p))
        assert config.quality_policy.coverage.global_threshold == 95

    def test_language_detection_fallback(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        (p / "Cargo.toml").write_text("[package]")
        config_path = self._write_config(p, "linters: {}")
        config = _load_yaml_config(config_path, str(p))
        assert "rust" in config.languages

    def test_explicit_languages(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        config_path = self._write_config(
            p,
            textwrap.dedent("""\
                languages:
                  - python
                  - go
            """),
        )
        config = _load_yaml_config(config_path, str(p))
        assert config.languages == ["python", "go"]

    def test_exemptions_and_extra_tier3(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        config_path = self._write_config(
            p,
            textwrap.dedent("""\
                exemptions:
                  foo.py:
                    codes: [E501]
                extra_tier3_linters:
                  - custom_lint
            """),
        )
        config = _load_yaml_config(config_path, str(p))
        assert "foo.py" in config.exemptions
        assert config.extra_tier3_linters == ["custom_lint"]


# ── load_config ──────────────────────────────────────────────────────────


class TestLoadConfig:
    def test_autodetect_no_yaml(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        (p / "pyproject.toml").write_text("")
        config = load_config(str(p))
        assert "python" in config.languages

    def test_uses_yaml_when_present(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        claude_dir = p / ".claude"
        claude_dir.mkdir()
        (claude_dir / "lintgate.yaml").write_text("total_timeout_ms: 5000\n")
        config = load_config(str(p))
        assert config.total_timeout_ms == 5000


# ── _auto_detect ─────────────────────────────────────────────────────────


class TestAutoDetect:
    def test_empty_project(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        config = _auto_detect(str(p))
        assert config.languages == []
        assert config.enabled_linters == {}

    def test_detects_python(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        (p / "setup.cfg").write_text("[metadata]")
        config = _auto_detect(str(p))
        assert "python" in config.languages


# ── load_controlplane_config ─────────────────────────────────────────────


class TestLoadControlplaneConfig:
    def _write_cp_config(self, tmp_path: object, content: str) -> str:
        p = tmp_path  # type: ignore[assignment]
        claude_dir = p / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "lintgate.yaml").write_text(content)
        return str(p)

    def test_no_yaml_returns_none(self, tmp_path: object) -> None:
        assert load_controlplane_config(str(tmp_path)) is None

    def test_empty_yaml_returns_none(self, tmp_path: object) -> None:
        cwd = self._write_cp_config(tmp_path, "")
        assert load_controlplane_config(cwd) is None

    def test_no_controlplane_section_returns_none(self, tmp_path: object) -> None:
        cwd = self._write_cp_config(tmp_path, "linters: {}")
        assert load_controlplane_config(cwd) is None

    def test_controlplane_not_dict_returns_none(self, tmp_path: object) -> None:
        cwd = self._write_cp_config(tmp_path, "controlplane: true")
        assert load_controlplane_config(cwd) is None

    def test_basic_controlplane(self, tmp_path: object) -> None:
        cwd = self._write_cp_config(
            tmp_path,
            textwrap.dedent("""\
                controlplane:
                  enabled: true
                  latency_budget_ms: 10000
                  advisory_default: false
            """),
        )
        cp = load_controlplane_config(cwd)
        assert cp is not None
        assert cp.enabled is True
        assert cp.latency_budget_ms == 10000
        assert cp.advisory_default is False

    def test_inquiry_config(self, tmp_path: object) -> None:
        cwd = self._write_cp_config(
            tmp_path,
            textwrap.dedent("""\
                controlplane:
                  enabled: true
                  inquiry:
                    theory_grounded_signals: true
                    prediction_tracking: true
                    session_gate: true
            """),
        )
        cp = load_controlplane_config(cwd)
        assert cp is not None
        assert cp.inquiry.theory_grounded_signals is True
        assert cp.inquiry.prediction_tracking is True
        assert cp.inquiry.session_gate is True
        assert cp.inquiry.living_context is False

    def test_global_memory_config(self, tmp_path: object) -> None:
        cwd = self._write_cp_config(
            tmp_path,
            textwrap.dedent("""\
                controlplane:
                  enabled: true
                  global_memory:
                    enabled: true
                    alpha: 0.8
                    decay_horizon: 100
                    ttl_days: 30
            """),
        )
        cp = load_controlplane_config(cwd)
        assert cp is not None
        assert cp.global_memory_enabled is True
        assert cp.global_memory_alpha == 0.8
        assert cp.global_memory_decay_horizon == 100
        assert cp.global_memory_ttl_days == 30

    def test_token_policy(self, tmp_path: object) -> None:
        cwd = self._write_cp_config(
            tmp_path,
            textwrap.dedent("""\
                controlplane:
                  enabled: true
                  token_policy:
                    hook_max_tokens: 500
                    include_pass_details: true
            """),
        )
        cp = load_controlplane_config(cwd)
        assert cp is not None
        assert cp.token_policy.hook_max_tokens == 500
        assert cp.token_policy.include_pass_details is True

    def test_habit_mode_config(self, tmp_path: object) -> None:
        cwd = self._write_cp_config(
            tmp_path,
            textwrap.dedent("""\
                controlplane:
                  enabled: true
                  habit_mode:
                    enabled: false
                    compact_threshold: 0.5
                    enter_score: 0.80
            """),
        )
        cp = load_controlplane_config(cwd)
        assert cp is not None
        assert cp.habit_mode_enabled is False
        assert cp.habit_mode_compact_threshold == 0.5
        assert cp.habit_mode_enter_score == 0.80

    def test_quality_gate_config(self, tmp_path: object) -> None:
        cwd = self._write_cp_config(
            tmp_path,
            textwrap.dedent("""\
                controlplane:
                  enabled: true
                  quality_gate:
                    enabled: true
                    block_push: false
                    staleness_threshold_s: 900
            """),
        )
        cp = load_controlplane_config(cwd)
        assert cp is not None
        assert cp.quality_gate.enabled is True
        assert cp.quality_gate.block_push is False
        assert cp.quality_gate.staleness_threshold_s == 900.0

    def test_channels_dict_config(self, tmp_path: object) -> None:
        cwd = self._write_cp_config(
            tmp_path,
            textwrap.dedent("""\
                controlplane:
                  enabled: true
                  channels:
                    lint:
                      enabled: true
                      blocking: true
                      timeout_ms: 5000
                      max_findings_shown: 3
                    test:
                      enabled: false
            """),
        )
        cp = load_controlplane_config(cwd)
        assert cp is not None
        assert cp.channels["lint"].enabled is True
        assert cp.channels["lint"].blocking is True
        assert cp.channels["lint"].timeout_ms == 5000
        assert cp.channels["lint"].max_findings_shown == 3
        assert cp.channels["test"].enabled is False

    def test_channels_bool_shorthand(self, tmp_path: object) -> None:
        cwd = self._write_cp_config(
            tmp_path,
            textwrap.dedent("""\
                controlplane:
                  enabled: true
                  channels:
                    behavior: false
            """),
        )
        cp = load_controlplane_config(cwd)
        assert cp is not None
        assert cp.channels["behavior"].enabled is False

    def test_coherence_channel_weights(self, tmp_path: object) -> None:
        cwd = self._write_cp_config(
            tmp_path,
            textwrap.dedent("""\
                controlplane:
                  enabled: true
                  coherence:
                    channel_weights:
                      structure: 0.4
                      behavior: 0.3
            """),
        )
        cp = load_controlplane_config(cwd)
        assert cp is not None
        assert cp.coherence_channel_weights == {"structure": 0.4, "behavior": 0.3}

    def test_hook_verbosity_and_compass(self, tmp_path: object) -> None:
        cwd = self._write_cp_config(
            tmp_path,
            textwrap.dedent("""\
                controlplane:
                  enabled: true
                  hook_verbosity: pulse
                  hook_pulse_interval: 10
                  hook_dispositions_enabled: false
                  compass:
                    enabled: true
                    staleness_hours: 48.0
            """),
        )
        cp = load_controlplane_config(cwd)
        assert cp is not None
        assert cp.hook_verbosity == "pulse"
        assert cp.hook_pulse_interval == 10
        assert cp.hook_dispositions_enabled is False
        assert cp.compass_enabled is True
        assert cp.compass_staleness_hours == 48.0

    def test_session_memory_and_constraint_threshold(self, tmp_path: object) -> None:
        cwd = self._write_cp_config(
            tmp_path,
            textwrap.dedent("""\
                controlplane:
                  enabled: true
                  session_memory: true
                  constraint_proposal_threshold: 10
                  severity_weighted_coherence: true
            """),
        )
        cp = load_controlplane_config(cwd)
        assert cp is not None
        assert cp.session_memory is True
        assert cp.constraint_proposal_threshold == 10
        assert cp.severity_weighted_coherence is True

    def test_malformed_yaml_returns_none(self, tmp_path: object) -> None:
        p = tmp_path  # type: ignore[assignment]
        claude_dir = p / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "lintgate.yaml").write_text("{{invalid yaml::")
        result = load_controlplane_config(str(p))
        assert result is None

    def test_channel_extra_settings(self, tmp_path: object) -> None:
        cwd = self._write_cp_config(
            tmp_path,
            textwrap.dedent("""\
                controlplane:
                  enabled: true
                  channels:
                    lint:
                      enabled: true
                      custom_key: custom_value
            """),
        )
        cp = load_controlplane_config(cwd)
        assert cp is not None
        assert cp.channels["lint"].settings == {"custom_key": "custom_value"}

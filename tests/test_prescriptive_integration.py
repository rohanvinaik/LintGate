"""Integration tests for PrescriptiveSpec hook and channel integration."""

from __future__ import annotations

from unittest import mock

from lintgate.specification.prescriptive.spec import (
    ForbiddenBehavior,
    Invariant,
    PrescriptiveSpec,
    pred_custom,
    pred_gt,
    save_spec,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _make_spec(target_key="mod::func", **overrides):
    defaults = {
        "spec_id": "test123",
        "target_key": target_key,
        "problem_class": "pure",
        "mode": "prospective",
        "invariants": [
            Invariant(
                "bounded", pred_gt("result", 0), "Result must be positive", "src", 0.8, "safety"
            ),
            Invariant(
                "typed", pred_custom("Must return int"), "Must return int", "src", 0.7, "alignment"
            ),
        ],
        "forbidden_behaviors": [
            ForbiddenBehavior(pred_custom("no mutation"), "Must not mutate input", "src", "hard"),
        ],
        "prescriptive_sigma": 5,
        "created_at": 1000.0,
    }
    defaults.update(overrides)
    return PrescriptiveSpec(**defaults)


def _setup_project_with_spec(tmp_path, target_key="mod::func"):
    """Create a temp project with a saved prescriptive spec."""
    spec = _make_spec(target_key=target_key)
    save_spec(str(tmp_path), spec)
    return spec


# ── PostToolUse advisory ─────────────────────────────────────────────


class TestPostToolUseAdvisory:
    def test_advisory_when_spec_exists(self, tmp_path):
        """PostToolUse emits [PSpec] advisory for function with spec."""
        from lintgate.hooks.posttooluse import _check_prescriptive_specs

        _setup_project_with_spec(tmp_path, target_key="mod::func")

        tool_input = {"file_path": str(tmp_path / "mod.py")}
        result = _check_prescriptive_specs(tool_input, str(tmp_path))

        assert result is not None
        assert "[PSpec]" in result
        assert "func" in result
        assert "prescriptive_spec_verify" in result

    def test_silent_when_no_spec(self, tmp_path):
        """No advisory when no specs exist."""
        from lintgate.hooks.posttooluse import _check_prescriptive_specs

        tool_input = {"file_path": str(tmp_path / "other.py")}
        result = _check_prescriptive_specs(tool_input, str(tmp_path))

        assert result is None

    def test_silent_for_non_python(self, tmp_path):
        """No advisory for non-Python files."""
        from lintgate.hooks.posttooluse import _check_prescriptive_specs

        _setup_project_with_spec(tmp_path)
        tool_input = {"file_path": str(tmp_path / "mod.txt")}
        result = _check_prescriptive_specs(tool_input, str(tmp_path))

        assert result is None


# ── PreToolUse obligation guidance ────────────────────────────────────


class TestPreToolUseObligations:
    def test_obligation_guidance(self, tmp_path):
        """PreToolUse emits invariant descriptions before write."""
        from lintgate.hooks.pre_tool import _check_prescriptive_obligations

        _setup_project_with_spec(tmp_path, target_key="mod::func")

        data = {"input": {"file_path": str(tmp_path / "mod.py")}}
        result = _check_prescriptive_obligations(data, str(tmp_path))

        assert "[PSpec]" in result
        assert "Obligations" in result

    def test_silent_when_no_spec(self, tmp_path):
        """No guidance when no specs exist."""
        from lintgate.hooks.pre_tool import _check_prescriptive_obligations

        data = {"input": {"file_path": str(tmp_path / "other.py")}}
        result = _check_prescriptive_obligations(data, str(tmp_path))

        assert result == ""

    def test_silent_for_non_python(self, tmp_path):
        """No guidance for non-Python files."""
        from lintgate.hooks.pre_tool import _check_prescriptive_obligations

        _setup_project_with_spec(tmp_path)
        data = {"input": {"file_path": str(tmp_path / "mod.json")}}
        result = _check_prescriptive_obligations(data, str(tmp_path))

        assert result == ""


# ── UserPromptSubmit primer ───────────────────────────────────────────


class TestUserPromptPrimer:
    def test_coverage_in_primer(self):
        """Primer includes PSpec count when specs exist."""
        from lintgate.hooks.user_prompt import _build_primer

        fake_runtime = mock.MagicMock()
        fake_runtime.mode = "normal"
        fake_runtime.habit_score = 0.0
        fake_runtime.active_files = []
        fake_runtime.blocking_issues = 0
        fake_runtime.approach_failures = 0
        fake_runtime.prediction_accuracy = -1.0
        fake_runtime.coherence_state = "stable"
        fake_runtime.prescriptive_spec_count = 5
        fake_runtime.prescriptive_coverage_ratio = 0.6

        with mock.patch("lintgate.runtime_state.load_runtime_state", return_value=fake_runtime):
            primer = _build_primer("/tmp/fake")

        assert primer is not None
        assert "PSpec" in primer
        assert "5 specs" in primer

    def test_no_coverage_when_zero(self):
        """No PSpec line when no specs exist."""
        from lintgate.hooks.user_prompt import _build_primer

        fake_runtime = mock.MagicMock()
        fake_runtime.mode = "normal"
        fake_runtime.habit_score = 0.0
        fake_runtime.active_files = []
        fake_runtime.blocking_issues = 0
        fake_runtime.approach_failures = 0
        fake_runtime.prediction_accuracy = -1.0
        fake_runtime.coherence_state = "stable"
        fake_runtime.prescriptive_spec_count = 0
        fake_runtime.prescriptive_coverage_ratio = 0.0

        with mock.patch("lintgate.runtime_state.load_runtime_state", return_value=fake_runtime):
            primer = _build_primer("/tmp/fake")

        if primer:
            assert "PSpec" not in primer


# ── PreCompact capsule ────────────────────────────────────────────────


class TestPreCompactCapsule:
    def test_capsule_includes_prescriptive_state(self, tmp_path):
        """Capsule has total_specs, problem_classes, mean_sigma."""
        from lintgate.hooks.pre_compact import _capture_prescriptive_state

        _setup_project_with_spec(tmp_path, target_key="mod::func_a")
        save_spec(
            str(tmp_path),
            _make_spec(
                spec_id="s2",
                target_key="mod::func_b",
                problem_class="stateful",
                prescriptive_sigma=10,
            ),
        )

        state = _capture_prescriptive_state(str(tmp_path))
        assert state is not None
        assert state["total_specs"] == 2
        assert state["problem_classes"]["pure"] == 1
        assert state["problem_classes"]["stateful"] == 1
        assert state["mean_prescriptive_sigma"] > 0

    def test_capsule_none_when_empty(self, tmp_path):
        """No prescriptive state when no specs."""
        from lintgate.hooks.pre_compact import _capture_prescriptive_state

        state = _capture_prescriptive_state(str(tmp_path))
        assert state is None


# ── Specification channel PSPEC findings ──────────────────────────────


class TestSpecChannelPSPEC:
    def test_pspec002_sigma_divergence(self, tmp_path):
        """PSPEC002 emitted when prescriptive/retrospective σ diverge >2×."""
        from lintgate.channels.specification_channel import _check_pspec002
        from lintgate.specification.types import (
            FunctionSpecification,
            RiskProfile,
            SpecCore,
            Traceability,
        )

        # Save a spec with prescriptive_sigma=5
        save_spec(str(tmp_path), _make_spec(prescriptive_sigma=5))

        # Create a func spec with retrospective sigma=20 (ratio=4.0, >2.0 threshold)
        fs = FunctionSpecification(
            function_key="mod::func",
            source_file="mod.py",
            core=SpecCore(estimated_sigma=20),
            risk=RiskProfile(),
            traceability=Traceability(),
        )

        findings = []
        _check_pspec002(fs, findings, str(tmp_path), threshold=2.0)

        assert len(findings) == 1
        assert findings[0].kind == "PSPEC002"
        assert "divergence" in findings[0].message.lower()

    def test_pspec002_not_emitted_when_converged(self, tmp_path):
        """PSPEC002 not emitted when sigma ratio is within bounds."""
        from lintgate.channels.specification_channel import _check_pspec002
        from lintgate.specification.types import (
            FunctionSpecification,
            RiskProfile,
            SpecCore,
            Traceability,
        )

        save_spec(str(tmp_path), _make_spec(prescriptive_sigma=5))

        fs = FunctionSpecification(
            function_key="mod::func",
            source_file="mod.py",
            core=SpecCore(estimated_sigma=6),  # ratio=1.2, within bounds
            risk=RiskProfile(),
            traceability=Traceability(),
        )

        findings = []
        _check_pspec002(fs, findings, str(tmp_path), threshold=2.0)
        assert len(findings) == 0

    def test_pspec003_advisory(self, tmp_path):
        """PSPEC003 emitted for unspecified function when specs exist."""
        from lintgate.channels.specification_channel import _check_pspec003
        from lintgate.specification.types import (
            FunctionSpecification,
            RiskProfile,
            SpecCore,
            Traceability,
        )

        # Save a spec for func_a
        _setup_project_with_spec(tmp_path, target_key="mod::func_a")

        # func_b has no spec
        fs = FunctionSpecification(
            function_key="mod::func_b",
            source_file="mod.py",
            core=SpecCore(),
            risk=RiskProfile(),
            traceability=Traceability(),
        )

        findings = []
        _check_pspec003(fs, findings, str(tmp_path))

        assert len(findings) == 1
        assert findings[0].kind == "PSPEC003"
        assert "func_b" in findings[0].message


# ── ExecutionCompass extended ─────────────────────────────────────────


class TestCompassExtended:
    def test_check_alignment_with_forbidden(self):
        """check_alignment_with_specs catches ForbiddenBehavior matches."""
        from lintgate.modes.execution_compass import ExecutionCompass

        compass = ExecutionCompass(
            true_north="Build correct code",
            toward=["test coverage"],
            away=["technical debt"],
            forbidden=["eval usage"],
        )

        spec = _make_spec()
        result = compass.check_alignment_with_specs("mutate the input data", specs=[spec])

        # "mutate" matches ForbiddenBehavior "Must not mutate input"
        assert not result["aligned"]
        assert len(result.get("prescriptive_violations", [])) >= 1

    def test_check_alignment_no_specs(self):
        """Without specs, behaves like base check_alignment."""
        from lintgate.modes.execution_compass import ExecutionCompass

        compass = ExecutionCompass(
            true_north="Build correct code",
            toward=[],
            away=[],
            forbidden=[],
        )

        result = compass.check_alignment_with_specs("safe operation", specs=None)
        assert result["aligned"]


# ── Config ────────────────────────────────────────────────────────────


class TestConfig:
    def test_config_has_prescriptive_fields(self):
        """ControlPlaneConfig has prescriptive spec fields with defaults."""
        from lintgate.controlplane.types import ControlPlaneConfig

        config = ControlPlaneConfig()
        assert config.prescriptive_spec_enabled is False
        assert config.prescriptive_spec_auto_compose_on_freeze is True
        assert config.prescriptive_spec_emit_advisory is True
        assert config.prescriptive_spec_sigma_divergence_threshold == 2.0

    def test_managed_section_registered(self):
        """prescriptive_rules in MANAGED_SECTION_IDS."""
        from lintgate.context.bootstrap_patches import MANAGED_SECTION_IDS

        assert "prescriptive_rules" in MANAGED_SECTION_IDS

    def test_runtime_state_has_prescriptive_fields(self):
        """RuntimeState has prescriptive_spec_count and prescriptive_coverage_ratio."""
        from lintgate.runtime_state import RuntimeState

        state = RuntimeState()
        assert state.prescriptive_spec_count == 0
        assert state.prescriptive_coverage_ratio == 0.0

    def test_config_parsed_from_yaml(self, tmp_path):
        """YAML prescriptive_spec section is parsed correctly."""
        import yaml

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "lintgate.yaml").write_text(
            yaml.dump(
                {
                    "controlplane": {
                        "enabled": True,
                        "prescriptive_spec": {
                            "enabled": True,
                            "auto_compose_on_freeze": False,
                            "sigma_divergence_threshold": 3.5,
                        },
                    }
                }
            )
        )

        from lintgate.config import load_controlplane_config

        cfg = load_controlplane_config(str(tmp_path))
        assert cfg is not None
        assert cfg.prescriptive_spec_enabled is True
        assert cfg.prescriptive_spec_auto_compose_on_freeze is False
        assert cfg.prescriptive_spec_sigma_divergence_threshold == 3.5


# ── Living context renderer ───────────────────────────────────────────


class TestLivingContextRenderer:
    def test_prescriptive_rules_section_renders(self, tmp_path):
        """prescriptive_rules managed section contains spec summaries."""
        from lintgate.context.bootstrap_render import _render_prescriptive_rules

        _setup_project_with_spec(tmp_path, target_key="mod::func_a")
        save_spec(
            str(tmp_path),
            _make_spec(spec_id="s2", target_key="mod::func_b"),
        )

        lines = _render_prescriptive_rules(str(tmp_path))
        assert len(lines) > 0
        content = "\n".join(lines)
        assert "LINTGATE:BEGIN prescriptive_rules" in content
        assert "LINTGATE:END prescriptive_rules" in content
        assert "mod::func_a" in content
        assert "mod::func_b" in content

    def test_prescriptive_rules_empty_when_no_specs(self, tmp_path):
        """No section when no specs exist."""
        from lintgate.context.bootstrap_render import _render_prescriptive_rules

        lines = _render_prescriptive_rules(str(tmp_path))
        assert lines == []

    def test_living_context_trigger_handler(self):
        """prescriptive_spec_composed trigger produces patch content."""
        from lintgate.context.bootstrap_patches import _patch_prescriptive_rules

        # With no existing section — bootstraps fresh
        result = _patch_prescriptive_rules(
            {},
            {"target_key": "mod::func", "problem_class": "pure", "summary": "bounded output"},
        )
        assert result is not None
        section_id, content = result
        assert section_id == "prescriptive_rules"
        assert "mod::func" in content
        assert "pure" in content

    def test_living_context_dedup(self):
        """Same target_key is not duplicated."""
        from lintgate.context.bootstrap_patches import ManagedSection, _patch_prescriptive_rules

        existing = ManagedSection(
            section_id="prescriptive_rules",
            version=1,
            content="## Prescriptive Specifications\n\n- `mod::func` (pure): bounded\n",
            start_pos=0,
            end_pos=100,
        )
        result = _patch_prescriptive_rules(
            {"prescriptive_rules": existing},
            {"target_key": "mod::func", "problem_class": "pure", "summary": "bounded"},
        )
        assert result is None  # Deduped


# ── Generation prompt consumer ────────────────────────────────────────


class TestGenerationPrompt:
    def test_generation_prompt_rendered(self):
        """Compile output includes generation_prompt when constraints exist."""
        from mcp_tools._prescriptive_impl import _render_generation_prompt

        constraints = [
            {
                "constraint_type": "must_not_use",
                "description": "Forbidden: no mutation",
                "priority": 1,
            },
            {
                "constraint_type": "must_use",
                "description": "Invariant: bounded output",
                "priority": 3,
            },
            {
                "constraint_type": "pattern",
                "description": "Algebraic law: idempotent",
                "priority": 4,
            },
        ]
        prompt = _render_generation_prompt("mod::func", constraints)

        assert "## Generation Constraints" in prompt
        assert "### Forbidden" in prompt
        assert "MUST NOT: Forbidden: no mutation" in prompt
        assert "### Required" in prompt
        assert "MUST: Invariant: bounded output" in prompt
        assert "### Patterns" in prompt
        assert "idempotent" in prompt

    def test_generation_prompt_empty(self):
        """Empty constraints → minimal prompt."""
        from mcp_tools._prescriptive_impl import _render_generation_prompt

        prompt = _render_generation_prompt("mod::func", [])
        assert "## Generation Constraints" in prompt
        assert "MUST" not in prompt


# ── PSPEC001 in channel ──────────────────────────────────────────────


class TestPSPEC001Channel:
    def test_pspec001_invariant_violation(self, tmp_path):
        """PSPEC001 emitted when AST check finds return type mismatch."""
        from lintgate.channels.specification_channel import _check_pspec001
        from lintgate.specification.prescriptive.spec import (
            Invariant,
            PrescriptiveSpec,
            pred_type,
            save_spec,
        )
        from lintgate.specification.types import (
            FunctionSpecification,
            RiskProfile,
            SpecCore,
            Traceability,
        )

        # Save spec requiring int return
        spec = PrescriptiveSpec(
            spec_id="t1",
            target_key="mod::compute",
            problem_class="pure",
            mode="prospective",
            invariants=[
                Invariant(
                    "typed",
                    pred_type("result", "str", "must return str"),
                    "must return str",
                    "src",
                    0.8,
                    "safety",
                ),
            ],
            prescriptive_sigma=2,
            created_at=1000.0,
        )
        save_spec(str(tmp_path), spec)

        # Create source file with int return annotation (mismatch)
        (tmp_path / "mod.py").write_text("def compute(x: int) -> int:\n    return x + 1\n")

        fs = FunctionSpecification(
            function_key="mod::compute",
            source_file=str(tmp_path / "mod.py"),
            core=SpecCore(),
            risk=RiskProfile(),
            traceability=Traceability(),
        )

        findings = []
        _check_pspec001(fs, findings, str(tmp_path))

        assert len(findings) == 1
        assert findings[0].kind == "PSPEC001"
        assert "typed" in findings[0].message

    def test_pspec001_no_violation_when_matching(self, tmp_path):
        """PSPEC001 not emitted when types match."""
        from lintgate.channels.specification_channel import _check_pspec001
        from lintgate.specification.prescriptive.spec import (
            Invariant,
            PrescriptiveSpec,
            pred_type,
            save_spec,
        )
        from lintgate.specification.types import (
            FunctionSpecification,
            RiskProfile,
            SpecCore,
            Traceability,
        )

        spec = PrescriptiveSpec(
            spec_id="t2",
            target_key="mod::compute",
            problem_class="pure",
            mode="prospective",
            invariants=[
                Invariant(
                    "typed",
                    pred_type("result", "int", "must return int"),
                    "returns int",
                    "src",
                    0.8,
                    "safety",
                ),
            ],
            prescriptive_sigma=2,
            created_at=1000.0,
        )
        save_spec(str(tmp_path), spec)

        (tmp_path / "mod.py").write_text("def compute(x: int) -> int:\n    return x + 1\n")

        fs = FunctionSpecification(
            function_key="mod::compute",
            source_file=str(tmp_path / "mod.py"),
            core=SpecCore(),
            risk=RiskProfile(),
            traceability=Traceability(),
        )

        findings = []
        _check_pspec001(fs, findings, str(tmp_path))
        assert len(findings) == 0


# ── Enriched retrospective compose ───────────────────────────────────


class TestEnrichedRetrospective:
    def test_boundary_obligations_from_design_signals(self):
        """Retrospective compose adds boundary obligations from design signals."""
        from lintgate.specification.prescriptive.spec import PrescriptiveSpecComposer

        class FakeDesign:
            boundary_points = 5
            equivalence_partitions = 3
            decision_rule_count = 0
            predicate_effect_links = 0

        class FakeCore:
            is_pure = True
            estimated_sigma = 10

        class FakeTestability:
            is_stateful = False

        class FakeTraceability:
            assertion_count = 2
            covering_tests = ["test_a", "test_b"]
            prescription_history = ["exact_value"]

        class FakeFS:
            function_key = "mod::func"
            core = FakeCore()
            testability = FakeTestability()
            traceability = FakeTraceability()
            design_signals = FakeDesign()

        class FakeCompass:
            directives = []
            axes = {}
            frozen_hash = "abc"

        composer = PrescriptiveSpecComposer()
        spec = composer.compose_retrospective(
            func_spec=FakeFS(),
            compass=FakeCompass(),
            theory_profile={},
        )

        kinds = [to.kind for to in spec.test_obligations]
        assert "exact_value" in kinds  # sigma gap
        assert "boundary" in kinds  # boundary_points > assertions
        assert "equivalence" in kinds  # equiv_parts > 1

        # Traceability metadata attached
        assert any("traceability:" in c for c in spec.theory_claims_used)

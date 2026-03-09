"""Targeted tests for tier selector project-scan helpers."""

from __future__ import annotations

import os
from unittest.mock import patch

from lintgate.tier_selector import (
    TIER_0_LINTERS,
    TIER_1_LINTERS,
    TIER_2_LINTERS,
    TIER_3_LINTERS,
    _collect_project_python_files,
    select_tier,
)
from lintgate.types import SKIP_TIER, ChangeClassification, ProjectConfig


def test_collect_project_python_files_excludes_backup_like_dirs(tmp_path) -> None:
    backup_dir = tmp_path / "archive_backup"
    backup_dir.mkdir()
    (backup_dir / "old.py").write_text("x = 1\n")

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "live.py").write_text("y = 2\n")

    files = _collect_project_python_files(str(tmp_path), limit=50)
    basenames = {os.path.basename(f) for f in files}
    assert "live.py" in basenames
    assert "old.py" not in basenames


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(**overrides: object) -> ProjectConfig:
    """Build a ProjectConfig with debounce disabled by default."""
    defaults: dict[str, object] = {
        "project_root": "/tmp/fake_project",
        "debounce": {"tier_0": 0.0, "tier_1": 0.0, "tier_2": 0.0, "tier_3": 0.0},
    }
    defaults.update(overrides)
    return ProjectConfig(**defaults)  # type: ignore[arg-type]


def _cc(**overrides: object) -> ChangeClassification:
    """Build a ChangeClassification with sensible defaults for Python files."""
    defaults: dict[str, object] = {
        "files_changed": ["app.py"],
        "files_by_language": {"python": ["app.py"]},
        "change_kind": "logic",
        "risk_level": "moderate",
    }
    defaults.update(overrides)
    return ChangeClassification(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TestSelectTierSpecification — covers every decision rule, boundary, and edge
# ---------------------------------------------------------------------------


class TestSelectTierSpecification:
    """Specification tests for select_tier covering all 14+ decision rules."""

    # ── Rule 1: risk_level == "none" → SKIP ──────────────────────────────

    def test_risk_none_returns_skip(self) -> None:
        result = select_tier(_cc(risk_level="none"), _cfg())
        assert result.skip is True
        assert result.name == "skip"

    # ── Rule 2: change_kind == "docs" → SKIP ─────────────────────────────

    def test_docs_change_returns_skip(self) -> None:
        result = select_tier(_cc(change_kind="docs", risk_level="moderate"), _cfg())
        assert result.skip is True
        assert result.name == "skip"

    # ── Rule 3: risk_level == "cosmetic" → SKIP ──────────────────────────

    def test_cosmetic_risk_returns_skip(self) -> None:
        result = select_tier(_cc(risk_level="cosmetic"), _cfg())
        assert result.skip is True
        assert result.name == "skip"

    # ── Rule 4: no python files + project-wide change kind → collect ─────

    def test_config_change_no_python_files_collects_project_files(self, tmp_path: object) -> None:
        """When change_kind is 'config' and no python files listed, the
        function should attempt to collect project python files."""
        with patch(
            "lintgate.tier_selector._collect_project_python_files",
            return_value=["collected.py"],
        ) as mock_collect:
            result = select_tier(
                _cc(
                    change_kind="config",
                    files_by_language={},
                    files_changed=["setup.cfg"],
                ),
                _cfg(project_root="/proj"),
            )
        mock_collect.assert_called_once_with("/proj")
        assert result.name == "tier_1_config"
        assert result.files == ["collected.py"]

    def test_dependency_change_no_python_files_collects_project_files(self) -> None:
        with patch(
            "lintgate.tier_selector._collect_project_python_files",
            return_value=["dep.py"],
        ):
            result = select_tier(
                _cc(
                    change_kind="dependency",
                    files_by_language={},
                    files_changed=["requirements.txt"],
                ),
                _cfg(),
            )
        assert result.name == "tier_1_dependency"

    def test_build_change_no_python_files_collects_project_files(self) -> None:
        with patch(
            "lintgate.tier_selector._collect_project_python_files",
            return_value=["build.py"],
        ):
            result = select_tier(
                _cc(
                    change_kind="build",
                    files_by_language={},
                    files_changed=["Makefile"],
                ),
                _cfg(),
            )
        assert result.name == "tier_1_build"

    # ── Rule 5: no python files after collection → SKIP ──────────────────

    def test_no_python_files_returns_skip(self) -> None:
        result = select_tier(
            _cc(
                change_kind="logic",
                files_by_language={"javascript": ["app.js"]},
            ),
            _cfg(),
        )
        assert result.skip is True
        assert result.name == "skip"

    def test_config_change_no_python_files_collected_returns_skip(self) -> None:
        """Project-wide change but collector returns empty list → SKIP."""
        with patch(
            "lintgate.tier_selector._collect_project_python_files",
            return_value=[],
        ):
            result = select_tier(_cc(change_kind="config", files_by_language={}), _cfg())
        assert result.skip is True

    def test_empty_files_by_language_returns_skip(self) -> None:
        result = select_tier(_cc(change_kind="logic", files_by_language={}), _cfg())
        assert result.skip is True

    # ── Rule 6: debounce fires → tier_0_debounced ────────────────────────

    def test_debounce_returns_tier0(self) -> None:
        with (
            patch("lintgate.tier_selector._should_debounce", return_value=True),
            patch("lintgate.tier_selector._record_lint_times"),
        ):
            cfg = _cfg(debounce={"tier_2": 5.0})
            result = select_tier(_cc(change_kind="logic", risk_level="moderate"), cfg)
        assert result.name == "tier_0_debounced"
        assert result.linters == TIER_0_LINTERS
        assert "Debounced" in result.reason

    def test_debounce_zero_interval_does_not_debounce(self) -> None:
        """Even if _should_debounce would return True, a 0.0 interval skips
        the debounce check entirely."""
        with (
            patch("lintgate.tier_selector._should_debounce", return_value=True) as mock_deb,
            patch("lintgate.tier_selector._record_lint_times"),
        ):
            cfg = _cfg(debounce={"tier_2": 0.0})
            result = select_tier(_cc(change_kind="logic", risk_level="moderate"), cfg)
        mock_deb.assert_not_called()
        assert result.name == "tier_2_logic"

    # ── Rule 7: change_kind == "config" → tier_1_config ──────────────────

    def test_config_change_returns_tier1_config(self) -> None:
        result = select_tier(_cc(change_kind="config", risk_level="moderate"), _cfg())
        assert result.name == "tier_1_config"
        assert result.linters == TIER_1_LINTERS
        assert result.files == ["app.py"]
        assert "Config" in result.reason

    # ── Rule 8: import_only → tier_1_import ──────────────────────────────

    def test_import_only_returns_tier1_import(self) -> None:
        result = select_tier(
            _cc(import_only=True, change_kind="logic", risk_level="moderate"),
            _cfg(),
        )
        assert result.name == "tier_1_import"
        assert result.linters == TIER_1_LINTERS
        assert "Import" in result.reason

    # ── Rule 9: change_kind == "dependency" → tier_1_dependency ──────────

    def test_dependency_change_returns_tier1_dependency(self) -> None:
        result = select_tier(_cc(change_kind="dependency", risk_level="moderate"), _cfg())
        assert result.name == "tier_1_dependency"
        assert result.linters == TIER_1_LINTERS
        assert "Dependency" in result.reason

    # ── Rule 10: change_kind == "build" → tier_1_build ───────────────────

    def test_build_change_returns_tier1_build(self) -> None:
        result = select_tier(_cc(change_kind="build", risk_level="moderate"), _cfg())
        assert result.name == "tier_1_build"
        assert result.linters == TIER_1_LINTERS
        assert "Build" in result.reason

    # ── Rule 11: risk_level == "architectural" → tier_3 ──────────────────

    def test_architectural_risk_returns_tier3(self) -> None:
        result = select_tier(_cc(risk_level="architectural", change_kind="logic"), _cfg())
        assert result.name == "tier_3_architectural"
        assert result.linters == TIER_3_LINTERS
        assert result.strictness == "strict"
        assert "critical path" in result.reason.lower() or "Architectural" in result.reason

    def test_architectural_risk_includes_extra_tier3_linters(self) -> None:
        cfg = _cfg(extra_tier3_linters=["custom_checker"])
        result = select_tier(_cc(risk_level="architectural", change_kind="logic"), cfg)
        assert result.name == "tier_3_architectural"
        assert "custom_checker" in result.linters
        assert result.linters == TIER_3_LINTERS + ["custom_checker"]

    # ── Rule 12: touches_pipeline_critical + non-docs/config → tier_3 ────

    def test_pipeline_critical_logic_change_returns_tier3(self) -> None:
        result = select_tier(
            _cc(
                touches_pipeline_critical=True,
                change_kind="logic",
                risk_level="moderate",
            ),
            _cfg(),
        )
        assert result.name == "tier_3_architectural"
        assert result.strictness == "strict"

    def test_pipeline_critical_build_change_returns_tier3(self) -> None:
        """Build is not in the exclusion set ("docs", "config") so
        pipeline-critical + build → tier_3."""
        # But build is matched earlier by rule 10 (change_kind == "build").
        # So this tests the priority ordering: build rule fires first.
        result = select_tier(
            _cc(
                touches_pipeline_critical=True,
                change_kind="build",
                risk_level="moderate",
            ),
            _cfg(),
        )
        # build rule (rule 10) fires before pipeline_critical (rule 12)
        assert result.name == "tier_1_build"

    # ── Boundary: pipeline_critical + change_kind in exclusion set ────────

    def test_pipeline_critical_config_change_not_tier3(self) -> None:
        """Boundary: touches_pipeline_critical=True but change_kind='config'
        is excluded from the tier_3 pipeline-critical rule. Falls to tier_1_config."""
        result = select_tier(
            _cc(
                touches_pipeline_critical=True,
                change_kind="config",
                risk_level="moderate",
            ),
            _cfg(),
        )
        # config rule fires first (rule 7), and even if it didn't,
        # change_kind="config" is excluded from pipeline-critical tier_3
        assert result.name == "tier_1_config"

    def test_pipeline_critical_docs_excluded_from_tier3(self) -> None:
        """Boundary: touches_pipeline_critical=True but change_kind='docs'
        is excluded — and docs rule fires even earlier → SKIP."""
        result = select_tier(
            _cc(
                touches_pipeline_critical=True,
                change_kind="docs",
                risk_level="moderate",
            ),
            _cfg(),
        )
        assert result.skip is True

    # ── Rule 13: test files only → tier_2_test (relaxed) ─────────────────

    def test_test_file_change_returns_tier2_relaxed(self) -> None:
        result = select_tier(
            _cc(
                touches_test_files=True,
                touches_pipeline_critical=False,
                change_kind="logic",
                risk_level="moderate",
            ),
            _cfg(),
        )
        assert result.name == "tier_2_test"
        assert result.linters == TIER_2_LINTERS
        assert result.strictness == "relaxed"
        assert "Test" in result.reason

    def test_test_file_plus_pipeline_critical_returns_tier3(self) -> None:
        """When touches_test_files AND touches_pipeline_critical are both True,
        the pipeline_critical rule (rule 12) fires first → tier_3."""
        result = select_tier(
            _cc(
                touches_test_files=True,
                touches_pipeline_critical=True,
                change_kind="logic",
                risk_level="moderate",
            ),
            _cfg(),
        )
        assert result.name == "tier_3_architectural"
        assert result.strictness == "strict"

    # ── Rule 14: risk_level == "structural" → tier_2_structural ──────────

    def test_structural_risk_returns_tier2_structural(self) -> None:
        result = select_tier(_cc(risk_level="structural", change_kind="refactor"), _cfg())
        assert result.name == "tier_2_structural"
        assert result.linters == TIER_2_LINTERS
        assert "Structural" in result.reason

    # ── Rule 15: change_kind == "logic" → tier_2_logic ───────────────────

    def test_logic_change_returns_tier2_logic(self) -> None:
        result = select_tier(_cc(change_kind="logic", risk_level="moderate"), _cfg())
        assert result.name == "tier_2_logic"
        assert result.linters == TIER_2_LINTERS
        assert "Logic" in result.reason

    # ── Rule 16: default fallback → tier_2_default ───────────────────────

    def test_default_fallback_returns_tier2_default(self) -> None:
        result = select_tier(_cc(change_kind="unknown_kind", risk_level="moderate"), _cfg())
        assert result.name == "tier_2_default"
        assert result.linters == TIER_2_LINTERS
        assert "Default" in result.reason

    def test_refactor_change_kind_returns_tier2_default(self) -> None:
        """A change_kind not matching any specific rule and risk_level not
        'structural' or 'architectural' falls to the default tier_2."""
        result = select_tier(_cc(change_kind="refactor", risk_level="moderate"), _cfg())
        assert result.name == "tier_2_default"

    # ── Edge cases ────────────────────────────────────────────────────────

    def test_empty_classification_defaults_still_select_tier(self) -> None:
        """ChangeClassification with all defaults (change_kind='logic',
        risk_level='moderate') but no files → SKIP because no python files."""
        cc = ChangeClassification()
        result = select_tier(cc, _cfg())
        # Default files_by_language is empty dict → no python files → SKIP
        assert result.skip is True

    def test_classification_with_python_files_and_defaults(self) -> None:
        """ChangeClassification defaults (logic/moderate) with python files
        → tier_2_logic."""
        cc = ChangeClassification(
            files_changed=["foo.py"],
            files_by_language={"python": ["foo.py"]},
        )
        result = select_tier(cc, _cfg())
        assert result.name == "tier_2_logic"

    def test_files_passed_through_to_tier(self) -> None:
        """The selected tier contains exactly the python files from the
        classification, not all files_changed."""
        py_files = ["a.py", "b.py", "c.py"]
        result = select_tier(
            _cc(
                files_changed=["a.py", "b.py", "c.py", "d.js"],
                files_by_language={"python": py_files, "javascript": ["d.js"]},
            ),
            _cfg(),
        )
        assert result.files == py_files

    def test_config_with_empty_debounce_dict(self) -> None:
        """Missing tier key in debounce dict → defaults to 0.0 → no debounce."""
        cfg = _cfg(debounce={})
        result = select_tier(_cc(change_kind="logic", risk_level="moderate"), cfg)
        assert result.name == "tier_2_logic"

    def test_skip_tier_sentinel_identity(self) -> None:
        """risk_level='none' returns the exact SKIP_TIER sentinel."""
        result = select_tier(_cc(risk_level="none"), _cfg())
        assert result is SKIP_TIER

    def test_docs_returns_exact_skip_tier_sentinel(self) -> None:
        result = select_tier(_cc(change_kind="docs", risk_level="moderate"), _cfg())
        assert result is SKIP_TIER

    def test_cosmetic_returns_exact_skip_tier_sentinel(self) -> None:
        result = select_tier(_cc(risk_level="cosmetic"), _cfg())
        assert result is SKIP_TIER

    # ── Priority ordering tests ───────────────────────────────────────────

    def test_none_risk_takes_precedence_over_everything(self) -> None:
        """risk_level='none' should SKIP even with pipeline_critical=True."""
        result = select_tier(
            _cc(
                risk_level="none",
                touches_pipeline_critical=True,
                change_kind="logic",
            ),
            _cfg(),
        )
        assert result is SKIP_TIER

    def test_docs_takes_precedence_over_architectural_risk(self) -> None:
        result = select_tier(
            _cc(
                change_kind="docs",
                risk_level="architectural",
            ),
            _cfg(),
        )
        assert result is SKIP_TIER

    def test_cosmetic_takes_precedence_over_logic_change(self) -> None:
        result = select_tier(_cc(risk_level="cosmetic", change_kind="logic"), _cfg())
        assert result is SKIP_TIER

    def test_config_change_kind_takes_precedence_over_import_only(self) -> None:
        """When both change_kind='config' and import_only=True, config fires first."""
        result = select_tier(
            _cc(change_kind="config", import_only=True, risk_level="moderate"),
            _cfg(),
        )
        assert result.name == "tier_1_config"

    def test_import_only_takes_precedence_over_dependency(self) -> None:
        """import_only check comes before change_kind='dependency'."""
        result = select_tier(
            _cc(
                import_only=True,
                change_kind="dependency",
                risk_level="moderate",
            ),
            _cfg(),
        )
        assert result.name == "tier_1_import"

    def test_architectural_risk_takes_precedence_over_test_files(self) -> None:
        result = select_tier(
            _cc(
                risk_level="architectural",
                touches_test_files=True,
                change_kind="logic",
            ),
            _cfg(),
        )
        assert result.name == "tier_3_architectural"

    def test_structural_risk_takes_precedence_over_default(self) -> None:
        result = select_tier(_cc(risk_level="structural", change_kind="unknown"), _cfg())
        assert result.name == "tier_2_structural"

    # ── Tier linter list exactness ────────────────────────────────────────

    def test_tier3_linters_exact_list(self) -> None:
        result = select_tier(_cc(risk_level="architectural", change_kind="logic"), _cfg())
        assert result.linters == TIER_3_LINTERS

    def test_tier2_linters_exact_list(self) -> None:
        result = select_tier(_cc(change_kind="logic", risk_level="moderate"), _cfg())
        assert result.linters == TIER_2_LINTERS

    def test_tier1_linters_exact_list(self) -> None:
        result = select_tier(_cc(change_kind="config", risk_level="moderate"), _cfg())
        assert result.linters == TIER_1_LINTERS

    def test_tier0_debounced_linters_exact_list(self) -> None:
        with (
            patch("lintgate.tier_selector._should_debounce", return_value=True),
            patch("lintgate.tier_selector._record_lint_times"),
        ):
            result = select_tier(
                _cc(change_kind="logic", risk_level="moderate"),
                _cfg(debounce={"tier_2": 5.0}),
            )
        assert result.linters == TIER_0_LINTERS

    # ── Strictness assertions ─────────────────────────────────────────────

    def test_tier3_strictness_is_strict(self) -> None:
        result = select_tier(_cc(risk_level="architectural", change_kind="logic"), _cfg())
        assert result.strictness == "strict"

    def test_tier2_test_strictness_is_relaxed(self) -> None:
        result = select_tier(
            _cc(
                touches_test_files=True,
                touches_pipeline_critical=False,
                risk_level="moderate",
            ),
            _cfg(),
        )
        assert result.strictness == "relaxed"

    def test_tier2_logic_strictness_is_normal(self) -> None:
        result = select_tier(_cc(change_kind="logic", risk_level="moderate"), _cfg())
        assert result.strictness == "normal"

    def test_tier1_strictness_is_normal(self) -> None:
        result = select_tier(_cc(change_kind="config", risk_level="moderate"), _cfg())
        assert result.strictness == "normal"

"""Schema contract tests for test_effectiveness tools."""

from __future__ import annotations

import json
from unittest.mock import patch

from lintgate.linters.test_effectiveness.types import (
    TEFF_SCHEMA_VERSION,
    EffectivenessWeakness,
    FunctionEffectiveness,
    MappingDiagnostics,
    QualityProfile,
    TestEffectivenessManifest,
)


def test_teff_schema_version_is_current():
    assert TEFF_SCHEMA_VERSION == "2.0.0"


def test_analyze_test_strength_schema_contract(tmp_path):
    """Verify analyze_test_strength output schema hasn't regressed."""
    from mcp_tools.test_effectiveness_tools import _analyze_test_strength_impl

    # Setup a minimal valid project so analysis reaches the success state
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("def hello(): return 1")

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text(
        "from src.app import hello\\ndef test_hello(): assert hello() == 1"
    )

    # Build a realistic manifest with proper types for JSON serialization
    fe = FunctionEffectiveness(
        function_name="hello",
        test_count=1,
        effectiveness_score=0.9,
        mutation_vulnerability=0.8,
        assertions=[],
        quality_profile=QualityProfile(semantic_ratio=1.0),
        weakness_taxonomy=EffectivenessWeakness.HEALTHY,
    )
    manifest = TestEffectivenessManifest(
        functions={"hello": fe},
        project_score=0.95,
        functions_analyzed=1,
        mutation_vulnerable_count=1,
        diagnostics=MappingDiagnostics(),
    )

    helpers = {
        "_validate_project_root": lambda p: str(tmp_path),
        "_json_dumps": lambda d, output_mode="": json.dumps(d),
    }

    with patch(
        "mcp_tools.test_effectiveness_tools.build_manifest_for_project",
        return_value=(
            manifest,
            [str(src / "app.py")],
            [str(tests / "test_app.py")],
            [str(src / "app.py")],
        ),
    ):
        result_json = _analyze_test_strength_impl(str(tmp_path), helpers)

    result = json.loads(result_json)

    assert result["state"] == "success"
    assert result["schema_version"] == "2.0.0"

    # Mutation integration is archived; keep stable placeholder keys.
    assert "mutation_ci_context" in result
    ci_context = result["mutation_ci_context"]
    assert ci_context["status"] == "archived"
    assert "note" in ci_context

    assert "mutation_hotspots" in result
    assert isinstance(result["mutation_hotspots"], list)
    assert result["mutation_hotspots"] == []

    # --- Pre-existing Schema Contracts (Regression Check) ---
    assert "summary" in result
    assert "effectiveness_score" in result["summary"]

    assert "top_vulnerable" in result
    assert isinstance(result["top_vulnerable"], list)

    assert "untested_functions" in result
    assert isinstance(result["untested_functions"], list)


def test_inspect_test_assertions_schema_contract(tmp_path):
    """Verify inspect_test_assertions output schema includes stable additive fields."""
    from mcp_tools.test_effectiveness_tools import _inspect_test_assertions_impl

    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("def hello(): return 1")

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text(
        "from src.app import hello\\ndef test_hello(): assert hello() == 1"
    )

    helpers = {
        "_validate_project_root": lambda p: str(tmp_path),
        "_json_dumps": lambda d, output_mode="": json.dumps(d),
    }

    # Use actual classifer which will find 0 assertions in our dummy file, but still output schema
    result_json = _inspect_test_assertions_impl(str(tmp_path), "tests/test_app.py", helpers)
    result = json.loads(result_json)

    assert result["schema_version"] == "2.0.0"
    assert "test_functions" in result
    assert "summary" in result

    assert "mutation_hotspots" in result
    assert isinstance(result["mutation_hotspots"], list)
    assert result["mutation_hotspots"] == []

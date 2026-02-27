"""Phase 2 schema contract tests. Ensure test_effectiveness tools output Mutation signals."""

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
    """Verify analyze_test_strength output schema hasn't regressed and includes Phase 2 fields."""
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

    # --- Phase 2 Additions ---
    assert "mutation_ci_context" in result
    ci_context = result["mutation_ci_context"]
    assert ci_context["run_state"] == "missing"
    assert ci_context["source"] == "missing"
    assert "score" in ci_context
    assert "total" in ci_context

    # --- Phase 4 Additions ---
    assert "equivalent_suspect" in ci_context
    assert "skipped_equivalent_policy" in ci_context
    assert "effective_total_for_score" in ci_context

    assert "mutation_hotspots" in result
    assert isinstance(result["mutation_hotspots"], list)
    if result["mutation_hotspots"]:
        # Check unified schema contract on fields if any exist
        hs = result["mutation_hotspots"][0]
        assert "run_id" in hs
        assert "file" in hs
        assert "line" in hs
        assert "function" in hs
        assert "operator" in hs
        assert "status" in hs
        assert "category" in hs
        assert "mutation_id" in hs
        assert isinstance(hs["test_ids"], list)
        assert "confidence" in hs

    # --- Pre-existing Schema Contracts (Regression Check) ---
    assert "summary" in result
    assert "effectiveness_score" in result["summary"]

    assert "top_vulnerable" in result
    assert isinstance(result["top_vulnerable"], list)

    assert "untested_functions" in result
    assert isinstance(result["untested_functions"], list)


def test_inspect_test_assertions_schema_contract(tmp_path):
    """Verify inspect_test_assertions output schema includes Phase 2 fields."""
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

    # --- Phase 2 Additions ---
    assert "mutation_hotspots" in result
    assert isinstance(result["mutation_hotspots"], list)

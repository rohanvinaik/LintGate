"""Regression tests for mutation-phase removal from bootstrap pipeline."""

from __future__ import annotations

import json

from lintgate.orchestration.bootstrap_pipeline import BootstrapPipeline
from lintgate.orchestration.bootstrap_state import BOOTSTRAP_DIR, PHASES, BootstrapState


def test_bootstrap_phase_list_has_no_mutation() -> None:
    assert "mutation" not in PHASES


def test_legacy_state_phase_migrates_from_mutation(tmp_path) -> None:
    project_root = str(tmp_path)

    # Build legacy on-disk state containing removed mutation phase/artifact.
    BOOTSTRAP_DIR.mkdir(parents=True, exist_ok=True)
    from lintgate.orchestration.bootstrap_state import _state_path

    state_path = _state_path(project_root)
    state_path.write_text(
        json.dumps(
            {
                "run_id": "legacy123",
                "project_root": project_root,
                "status": "running",
                "phase": "mutation",
                "files_processed": {"src/a.py": "mutation"},
                "artifacts": {
                    "generated_test_dir": "tests/generated",
                    "mutation_output_path": "legacy.json",
                    "proposal_output_path": None,
                    "test_files": [],
                },
            }
        )
    )

    loaded = BootstrapState.load(project_root)
    assert loaded.phase == "contracts"
    assert loaded.files_processed["src/a.py"] == "contracts"
    assert not hasattr(loaded.artifacts, "mutation_output_path")


def test_pipeline_completes_without_mutation_phase(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "example.py").write_text("def hello():\n    return 1\n")

    pipeline = BootstrapPipeline(str(tmp_path))
    result = pipeline.run(dry_run=True)

    assert result.status == "dry_run"
    assert result.phase == "complete"
    assert pipeline.state.phase == "complete"

"""Selection and simple filesystem helpers for the test channel."""

from __future__ import annotations

import os
from pathlib import Path

from lintgate.controlplane.types import RepairAction
from lintgate.types import LintIssue


def _check_missing_tests(
    changed_files: list[str],
    project_root: str,
    findings: list[LintIssue],
    repairs: list[RepairAction],
) -> None:
    """Check for source files without corresponding tests and propose skeletons."""
    for src_file in changed_files:
        if not (_is_source_file(src_file, project_root) and not _has_test(src_file, project_root)):
            continue
        findings.append(
            LintIssue(
                linter="test_channel",
                kind="missing_test",
                message=f"No test file found for {os.path.basename(src_file)}",
                file=src_file,
                severity="informational",
            )
        )
        try:
            from lintgate.controlplane.test_archetype_selector import select_archetypes

            archetypes = select_archetypes(src_file, project_root)
            if archetypes:
                repairs.append(
                    RepairAction(
                        channel="tests",
                        kind="create_test_skeleton",
                        summary=(
                            f"Create test skeleton for {os.path.basename(src_file)} "
                            f"({archetypes[0].name})"
                        ),
                        payload={
                            "source_file": src_file,
                            "archetypes": [arch.name for arch in archetypes],
                        },
                        safe=True,
                    )
                )
        except Exception:
            pass


def _discover_fallback_test_targets(project_root: str) -> list[str]:
    """Discover broad test targets when impacted-test mapping finds none."""
    root = Path(project_root)
    targets: list[str] = []
    for dirname in ("tests", "test"):
        candidate = root / dirname
        if candidate.is_dir():
            targets.append(str(candidate))
    if targets:
        return targets
    for candidate in sorted(root.glob("test_*.py")):
        if candidate.is_file():
            targets.append(str(candidate))
    return targets


def _select_tests_to_run(
    impacted_tests: list[str],
    project_root: str,
    cov_cfg: dict[str, object] | None,
    surface: str,
    findings: list[LintIssue],
) -> list[str]:
    """Choose test targets. Symbol gate in MCP/CI falls back to broad suite."""
    if impacted_tests:
        return impacted_tests
    if not isinstance(cov_cfg, dict):
        return []
    if not (cov_cfg.get("symbol_enabled") and surface in ("mcp", "ci")):
        return []
    fallback_targets = _discover_fallback_test_targets(project_root)
    if fallback_targets:
        findings.append(
            LintIssue(
                linter="test_channel",
                kind="symbol_gate_fallback",
                message="No impacted tests detected; running fallback test targets for symbol gate.",
                severity="informational",
                evidence={"targets": fallback_targets[:4], "surface": surface},
            )
        )
    return fallback_targets


def _no_test_files_exist(project_root: str) -> bool:
    """Check whether the project has zero test files anywhere."""
    root = Path(project_root)
    for dirname in ("tests", "test"):
        test_dir = root / dirname
        if test_dir.is_dir():
            for _ in test_dir.rglob("test_*.py"):
                return False
            for _ in test_dir.rglob("*_test.py"):
                return False
    for _ in root.glob("test_*.py"):
        return False
    for _ in root.rglob("test_*.py"):
        return False
    return True


def _is_source_file(filepath: str, project_root: str) -> bool:
    """Check if a file is a Python source file (not test, not config)."""
    del project_root
    path = Path(filepath)
    if path.suffix != ".py":
        return False
    if path.stem.startswith("test_") or path.name == "conftest.py":
        return False
    if path.stem.startswith("__"):
        return False
    return path.stem not in ("setup", "conftest")


def _has_test(source_file: str, project_root: str) -> bool:
    """Check if a source file has a corresponding test file."""
    from lintgate.channels._test_channel_impact import find_impacted_tests

    return len(find_impacted_tests([source_file], project_root)) > 0

"""Tests for intent-aware workflows in onboarding_tools.py."""

from __future__ import annotations

import os

from mcp_tools.onboarding_tools import _build_intent_workflow


def test_build_intent_workflow_explore():
    path = "/tmp/test_project"
    abs_path = os.path.abspath(path)
    workflow = _build_intent_workflow("explore", path)

    assert len(workflow) == 2
    assert workflow[0]["id"] == "health_check"
    assert workflow[0]["tool"] == "controlplane_run"
    assert workflow[0]["arguments"]["path"] == abs_path
    assert workflow[1]["id"] == "scaffold"
    assert workflow[1]["tool"] == "scaffold_config"


def test_build_intent_workflow_fix_bug():
    path = "/tmp/test_project"
    os.path.abspath(path)
    workflow = _build_intent_workflow("fix_bug", path)

    assert len(workflow) == 2
    assert workflow[0]["id"] == "find_bugs"
    assert workflow[0]["tool"] == "controlplane_run"
    assert "lint,test,behavior" in workflow[0]["arguments"]["channels"]
    assert workflow[1]["id"] == "autofix"
    assert workflow[1]["tool"] == "lint_fix"


def test_build_intent_workflow_add_feature():
    path = "/tmp/test_project"
    os.path.abspath(path)
    workflow = _build_intent_workflow("add_feature", path)

    assert len(workflow) == 2
    assert workflow[0]["id"] == "baseline_check"
    assert workflow[1]["id"] == "test_gen"
    assert workflow[1]["tool"] == "bootstrap_context_files"


def test_build_intent_workflow_refactor():
    path = "/tmp/test_project"
    os.path.abspath(path)
    workflow = _build_intent_workflow("refactor", path)

    assert len(workflow) == 2
    assert workflow[0]["id"] == "structure_check"
    assert "structure,lint" in workflow[0]["arguments"]["channels"]
    assert workflow[1]["id"] == "perf_check"


def test_build_intent_workflow_security():
    path = "/tmp/test_project"
    os.path.abspath(path)
    workflow = _build_intent_workflow("security", path)

    assert len(workflow) == 2
    assert workflow[0]["id"] == "security_scan"
    assert workflow[0]["arguments"]["strictness"] == "strict"
    assert workflow[1]["id"] == "dep_audit"
    assert workflow[1]["arguments"]["channels"] == "dependency"


def test_build_intent_workflow_unknown_intent_falls_back_to_explore():
    path = "/tmp/test_project"
    workflow = _build_intent_workflow("unknown_intent", path)
    explore_workflow = _build_intent_workflow("explore", path)

    assert workflow == explore_workflow

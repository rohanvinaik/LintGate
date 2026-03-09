"""Standalone setup_github_quality function extracted from onboarding_tools.

This module exists so that static analyzers (ty, mypy) can resolve
``from mcp_tools.setup_github_quality import setup_github_quality``
without relying on the dynamic ``globals().update()`` trick used by
``mcp_server.py``.
"""

from __future__ import annotations

import json
import os
from typing import Any

from mcp_tools.quality_helpers import (
    _apply_managed_artifact,
    _build_quality_guidance,
    _compute_gitignore_additions,
    _detect_github_remote,
    _detect_project_layout,
    _detect_sonar_scanner,
    _detect_subprocess_usage,
    _generate_badge_markdown,
    _generate_clusterfuzzlite_workflow,
    _generate_codeclimate_yml,
    _generate_codeql_workflow,
    _generate_coveragerc,
    _generate_dependabot_yml,
    _generate_gitleaks_toml,
    _generate_pypi_publish_workflow,
    _generate_qlty_toml,
    _generate_qlty_workflow,
    _generate_quality_infra_gate_workflow,
    _generate_scorecard_workflow,
    _generate_security_md,
    _generate_security_workflow,
    _generate_sonar_properties,
    _generate_sonar_workflow,
    _generate_tests_workflow,
    _inject_badges_into_readme,
    _run_sonar_scanner,
    _write_pre_push_hook,
)


def setup_github_quality(
    path: str,
    write: bool = False,
    sonar_token: str | None = None,
    *,
    _helpers: dict[str, Any] | None = None,
) -> str:
    """Set up GitHub code quality badges and infrastructure for a project."""
    if _helpers and "_validate_project_root" in _helpers:
        project_root: str = _helpers["_validate_project_root"](path)
    else:
        project_root = path

    github = _detect_github_remote(project_root)
    layout = _detect_project_layout(project_root)
    is_tool_runner = _detect_subprocess_usage(project_root)

    # 1. Define core artifacts to manage
    # Each entry: (relative_path, generator_fn_call, dict_key)
    artifact_definitions = [
        (".codeclimate.yml", _generate_codeclimate_yml(layout), "codeclimate_yml"),
        (
            "sonar-project.properties",
            _generate_sonar_properties(github, layout),
            "sonar_properties",
        ),
        (".coveragerc", _generate_coveragerc(), "coveragerc"),
        (".gitleaks.toml", _generate_gitleaks_toml(), "gitleaks_toml"),
        (
            ".github/workflows/sonarcloud.yml",
            _generate_sonar_workflow(),
            "sonarcloud_workflow",
        ),
        (".github/workflows/qlty.yml", _generate_qlty_workflow(), "qlty_workflow"),
        (".github/workflows/tests.yml", _generate_tests_workflow(), "tests_workflow"),
        (
            ".github/workflows/security-lite.yml",
            _generate_security_workflow(),
            "security_workflow",
        ),
        (
            ".github/workflows/scorecard.yml",
            _generate_scorecard_workflow(),
            "scorecard_workflow",
        ),
        (
            ".github/workflows/codeql.yml",
            _generate_codeql_workflow(),
            "codeql_workflow",
        ),
        (
            ".github/workflows/cif.yml",
            _generate_clusterfuzzlite_workflow(),
            "clusterfuzzlite_workflow",
        ),
        (
            ".github/workflows/quality-infra-gate.yml",
            _generate_quality_infra_gate_workflow(),
            "quality_infra_gate_workflow",
        ),
        (
            ".github/workflows/pypi-publish.yml",
            _generate_pypi_publish_workflow(),
            "pypi_publish_workflow",
        ),
        (".github/dependabot.yml", _generate_dependabot_yml(), "dependabot"),
        ("SECURITY.md", _generate_security_md(github), "security_md"),
        (
            ".qlty/qlty.toml",
            _generate_qlty_toml(layout, is_tool_runner=is_tool_runner),
            "qlty",
        ),
        (".qlty/.gitignore", "*\n!.gitignore\n!qlty.toml\n", "qlty_gitignore"),
    ]

    # 2. Apply artifacts using loop to reduce complexity
    results: dict[str, Any] = {}
    for rel_path, content, key in artifact_definitions:
        full_path = os.path.join(project_root, rel_path)
        results[key] = _apply_managed_artifact(full_path, content, os.path.exists(full_path), write)

    # 3. Handle special artifacts (git hooks, gitignore, badges)
    results["pre_push_hook"] = _write_pre_push_hook(project_root, write)

    gi_delta = _compute_gitignore_additions(project_root)
    if write and gi_delta.get("missing"):
        gi_path = os.path.join(project_root, ".gitignore")
        with open(gi_path, "a") as f:
            f.write("\n" + "\n".join(gi_delta["missing"]) + "\n")
        gi_delta["status"] = "augmented"
    elif write and not gi_delta.get("missing"):
        gi_delta["status"] = "no_changes_needed"
    results["gitignore"] = gi_delta

    badge_markdown = _generate_badge_markdown(github, layout)
    badges = _inject_badges_into_readme(project_root, badge_markdown, write)
    if not github.get("detected") and badges["status"] == "preview":
        badges["status"] = "skipped_no_remote"
    results["badges"] = badges

    # 4. Optional: Initial SonarCloud analysis
    scanner_result = {"status": "preview"}
    if write and sonar_token:
        scanner_path = _detect_sonar_scanner()
        if scanner_path:
            scanner_result = _run_sonar_scanner(project_root, sonar_token, scanner_path)
        else:
            scanner_result = {"status": "scanner_not_found"}
    elif write and not sonar_token:
        scanner_result = {"status": "skipped"}
    results["scanner"] = scanner_result

    # 5. Build final output with compatibility mapping
    guidance = _build_quality_guidance(github, layout, _detect_sonar_scanner())

    next_actions = [
        "1. Commit the generated quality infrastructure to your repository.",
        "2. Add SONAR_TOKEN to your GitHub repository secrets if using SonarCloud.",
        "3. Run 'qlty check --all' locally to verify the baseline.",
    ]

    # Map internal keys back to legacy external contract keys
    # e.g., codeclimate_yml -> codeclimate, sonar_properties -> sonar
    output = {
        "status": "written" if write else "preview",
        "codeclimate": results.get("codeclimate_yml"),
        "sonar": results.get("sonar_properties"),
        "coveragerc": results.get("coveragerc"),
        "gitleaks": results.get("gitleaks_toml"),
        "github_actions": {
            "sonarcloud": results.get("sonarcloud_workflow"),
            "qlty": results.get("qlty_workflow"),
            "tests": results.get("tests_workflow"),
            "security": results.get("security_workflow"),
            "scorecard": results.get("scorecard_workflow"),
            "codeql": results.get("codeql_workflow"),
            "clusterfuzzlite": results.get("clusterfuzzlite_workflow"),
            "quality_infra_gate": results.get("quality_infra_gate_workflow"),
            "pypi_publish": results.get("pypi_publish_workflow"),
        },
        "dependabot": results.get("dependabot"),
        "security_md": results.get("security_md"),
        "pre_push_hook": results.get("pre_push_hook"),
        "gitignore": results.get("gitignore"),
        "badges": results.get("badges"),
        "scanner": results.get("scanner"),
    }

    # Re-normalize QLTY output for older consumers
    qlty_res = results.get("qlty", {}).copy()

    # We never want to overwrite qlty.toml automatically in setup_github_quality
    # if it already exists, so "drift_repaired" should revert to already_exists
    # to maintain backward compatibility and avoid unexpected overwrites
    if qlty_res.get("status") == "drift_repaired":
        qlty_res["status"] = "already_exists"

    qlty_res["local_only"] = False
    qlty_res["tracked_in_git"] = True
    output["qlty"] = qlty_res

    # Provide additive merging of new/internal keys for forward compatibility
    output.update(results)

    # After update, force the re-normalized qlty to persist at the top level
    output["qlty"] = qlty_res

    output["guidance"] = guidance
    output["next_actions"] = next_actions

    if _helpers and "_json_dumps" in _helpers:
        return _helpers["_json_dumps"](output, output_mode="compact")
    return json.dumps(output, indent=2)

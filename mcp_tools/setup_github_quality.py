"""Standalone setup_github_quality function extracted from onboarding_tools.

This module exists so that static analyzers (ty, mypy) can resolve
``from mcp_tools.setup_github_quality import setup_github_quality``
without relying on the dynamic ``globals().update()`` trick used by
``mcp_server.py``.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any

from mcp_tools.onboarding_tools import (
    _apply_managed_artifact,
    _build_quality_guidance,
    _compute_gitignore_additions,
    _detect_github_remote,
    _detect_project_layout,
    _detect_sonar_scanner,
    _detect_subprocess_usage,
    _generate_badge_markdown,
    _generate_codeclimate_yml,
    _generate_coveragerc,
    _generate_dependabot_yml,
    _generate_gitleaks_toml,
    _generate_pre_push_hook,  # noqa: F401 — re-exported for test access
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
    """Set up GitHub code quality badges and infrastructure for a project.

    This is the extracted core logic.  When called from the MCP tool layer
    ``_helpers`` is supplied by the ``register()`` wrapper.  When called
    directly from tests ``_helpers`` may be ``None``, in which case
    ``path`` is used as-is (no validation).
    """
    if _helpers and "_validate_project_root" in _helpers:
        project_root: str = _helpers["_validate_project_root"](path)
    else:
        project_root = path

    github = _detect_github_remote(project_root)
    layout = _detect_project_layout(project_root)

    # Detect if project is a tool-runner (uses subprocess)
    is_tool_runner = _detect_subprocess_usage(project_root)

    # --- .codeclimate.yml ---
    cc_path = os.path.join(project_root, ".codeclimate.yml")
    cc_exists = os.path.exists(cc_path)
    cc_content = _generate_codeclimate_yml(layout)
    cc_result: dict[str, Any] = {"path": cc_path}

    if cc_exists:
        cc_result["status"] = "already_exists"
    elif write:
        with open(cc_path, "w") as f:
            f.write(cc_content)
        cc_result["status"] = "written"
    else:
        cc_result["status"] = "preview"
        cc_result["content"] = cc_content

    # --- sonar-project.properties ---
    sonar_path = os.path.join(project_root, "sonar-project.properties")
    sonar_exists = os.path.exists(sonar_path)
    sonar_content = _generate_sonar_properties(github, layout)
    sonar_result: dict[str, Any] = {"path": sonar_path}

    if sonar_exists:
        sonar_result["status"] = "already_exists"
    elif write:
        with open(sonar_path, "w") as f:
            f.write(sonar_content)
        sonar_result["status"] = "written"
    else:
        sonar_result["status"] = "preview"
        sonar_result["content"] = sonar_content

    # --- .coveragerc ---
    coveragerc_path = os.path.join(project_root, ".coveragerc")
    coveragerc_exists = os.path.exists(coveragerc_path)
    coveragerc_content = _generate_coveragerc()
    coveragerc_result: dict[str, Any] = {"path": coveragerc_path}

    if coveragerc_exists:
        coveragerc_result["status"] = "already_exists"
    elif write:
        with open(coveragerc_path, "w") as f:
            f.write(coveragerc_content)
        coveragerc_result["status"] = "written"
    else:
        coveragerc_result["status"] = "preview"
        coveragerc_result["content"] = coveragerc_content

    # --- .gitleaks.toml ---
    gitleaks_path = os.path.join(project_root, ".gitleaks.toml")
    gitleaks_exists = os.path.exists(gitleaks_path)
    gitleaks_content = _generate_gitleaks_toml()
    gitleaks_result: dict[str, Any] = {"path": gitleaks_path}

    if gitleaks_exists:
        gitleaks_result["status"] = "already_exists"
    elif write:
        with open(gitleaks_path, "w") as f:
            f.write(gitleaks_content)
        gitleaks_result["status"] = "written"
    else:
        gitleaks_result["status"] = "preview"
        gitleaks_result["content"] = gitleaks_content

    # --- .github/workflows/sonarcloud.yml ---
    workflow_path = os.path.join(project_root, ".github", "workflows", "sonarcloud.yml")
    workflow_exists = os.path.exists(workflow_path)
    workflow_content = _generate_sonar_workflow(layout)
    workflow_result: dict[str, Any] = {"path": workflow_path}

    if workflow_exists:
        workflow_result["status"] = "already_exists"
    elif write:
        os.makedirs(os.path.dirname(workflow_path), exist_ok=True)
        with open(workflow_path, "w") as f:
            f.write(workflow_content)
        workflow_result["status"] = "written"
    else:
        workflow_result["status"] = "preview"
        workflow_result["content"] = workflow_content

    # --- .github/workflows/tests.yml ---
    tests_workflow_path = os.path.join(project_root, ".github", "workflows", "tests.yml")
    tests_workflow_exists = os.path.exists(tests_workflow_path)
    tests_workflow_content = _generate_tests_workflow(layout)
    tests_workflow_result: dict[str, Any] = {"path": tests_workflow_path}

    if tests_workflow_exists:
        tests_workflow_result["status"] = "already_exists"
    elif write:
        os.makedirs(os.path.dirname(tests_workflow_path), exist_ok=True)
        with open(tests_workflow_path, "w") as f:
            f.write(tests_workflow_content)
        tests_workflow_result["status"] = "written"
    else:
        tests_workflow_result["status"] = "preview"
        tests_workflow_result["content"] = tests_workflow_content

    # --- .github/workflows/qlty.yml ---
    qlty_workflow_path = os.path.join(project_root, ".github", "workflows", "qlty.yml")
    qlty_workflow_exists = os.path.exists(qlty_workflow_path)
    qlty_workflow_content = _generate_qlty_workflow()
    qlty_workflow_result: dict[str, Any] = {"path": qlty_workflow_path}

    if qlty_workflow_exists:
        qlty_workflow_result["status"] = "already_exists"
    elif write:
        os.makedirs(os.path.dirname(qlty_workflow_path), exist_ok=True)
        with open(qlty_workflow_path, "w") as f:
            f.write(qlty_workflow_content)
        qlty_workflow_result["status"] = "written"
    else:
        qlty_workflow_result["status"] = "preview"
        qlty_workflow_result["content"] = qlty_workflow_content

    # --- .github/workflows/security-lite.yml ---
    security_workflow_path = os.path.join(
        project_root, ".github", "workflows", "security-lite.yml"
    )
    security_workflow_exists = os.path.exists(security_workflow_path)
    security_workflow_content = _generate_security_workflow(
        layout,
        is_tool_runner=is_tool_runner,
        project_root=project_root,
    )
    security_workflow_result: dict[str, Any] = {"path": security_workflow_path}

    if security_workflow_exists:
        security_workflow_result["status"] = "already_exists"
    elif write:
        os.makedirs(os.path.dirname(security_workflow_path), exist_ok=True)
        with open(security_workflow_path, "w") as f:
            f.write(security_workflow_content)
        security_workflow_result["status"] = "written"
    else:
        security_workflow_result["status"] = "preview"
        security_workflow_result["content"] = security_workflow_content

    # --- .github/workflows/scorecard.yml ---
    scorecard_path = os.path.join(
        project_root, ".github", "workflows", "scorecard.yml"
    )
    scorecard_exists = os.path.exists(scorecard_path)
    scorecard_content = _generate_scorecard_workflow()

    scorecard_result = _apply_managed_artifact(
        scorecard_path, scorecard_content, scorecard_exists, write,
    )

    # --- .github/workflows/quality-infra-gate.yml ---
    qi_gate_path = os.path.join(
        project_root, ".github", "workflows", "quality-infra-gate.yml"
    )
    qi_gate_exists = os.path.exists(qi_gate_path)
    qi_gate_content = _generate_quality_infra_gate_workflow()
    qi_gate_result: dict[str, Any] = _apply_managed_artifact(
        qi_gate_path, qi_gate_content, qi_gate_exists, write,
    )

    # --- .github/dependabot.yml ---
    dependabot_path = os.path.join(project_root, ".github", "dependabot.yml")
    dependabot_exists = os.path.exists(dependabot_path)
    dependabot_content = _generate_dependabot_yml()
    dependabot_result: dict[str, Any] = _apply_managed_artifact(
        dependabot_path, dependabot_content, dependabot_exists, write,
    )

    # --- SECURITY.md ---
    security_md_path = os.path.join(project_root, "SECURITY.md")
    security_md_exists = os.path.exists(security_md_path)
    security_md_content = _generate_security_md(github)
    security_md_result: dict[str, Any] = {"path": security_md_path}

    if security_md_exists:
        # User-customizable — don't drift-repair
        security_md_result["status"] = "already_exists"
    elif write:
        with open(security_md_path, "w") as f:
            f.write(security_md_content)
        security_md_result["status"] = "written"
    else:
        security_md_result["status"] = "preview"
        security_md_result["content"] = security_md_content

    # --- .githooks/pre-push ---
    pre_push_hook_result = _write_pre_push_hook(project_root, write=write)

    # --- .qlty/qlty.toml ---
    qlty_dir = os.path.join(project_root, ".qlty")
    qlty_path = os.path.join(qlty_dir, "qlty.toml")
    qlty_exists = os.path.exists(qlty_path)
    qlty_content = _generate_qlty_toml(layout, is_tool_runner=is_tool_runner)
    qlty_result: dict[str, Any] = {
        "path": qlty_path,
        "is_tool_runner": is_tool_runner,
        "local_only": False,
        "tracked_in_git": True,
        "note": ".qlty/qlty.toml is intended to be committed so CI matches local triage.",
    }

    qlty_gitignore = os.path.join(qlty_dir, ".gitignore")
    qlty_gitignore_written = False
    if qlty_exists:
        qlty_result["status"] = "already_exists"
    elif write:
        os.makedirs(qlty_dir, exist_ok=True)
        with open(qlty_path, "w") as f:
            f.write(qlty_content)
        qlty_result["status"] = "written"
    else:
        qlty_result["status"] = "preview"
        qlty_result["content"] = qlty_content

    if write:
        os.makedirs(qlty_dir, exist_ok=True)
        if not os.path.exists(qlty_gitignore):
            with open(qlty_gitignore, "w") as f:
                f.write("logs\nout\nplugin_cachedir\nresults\n")
            qlty_gitignore_written = True
    qlty_result["gitignore_path"] = qlty_gitignore
    qlty_result["gitignore_written"] = qlty_gitignore_written

    # --- .gitignore ---
    gi_info = _compute_gitignore_additions(project_root)
    gi_result: dict[str, Any] = {
        "existing_pattern_count": gi_info["existing_pattern_count"],
        "additions_count": len(gi_info["additions"]),
        "already_present_count": len(gi_info["already_present"]),
    }

    if not gi_info["additions"]:
        gi_result["status"] = "no_changes_needed"
    elif write:
        gi_path = os.path.join(project_root, ".gitignore")
        with open(gi_path, "a") as f:
            if gi_info["gitignore_exists"]:
                f.write("\n")
            f.write("# Added by LintGate setup_github_quality\n")
            for pat in gi_info["additions"]:
                f.write(f"{pat}\n")
        gi_result["status"] = "augmented" if gi_info["gitignore_exists"] else "created"
        gi_result["patterns_added"] = gi_info["additions"]
    else:
        gi_result["status"] = "preview"
        gi_result["additions"] = gi_info["additions"]

    # --- README badges ---
    badge_result: dict[str, Any] = {}
    if github.get("detected"):
        badge_md = _generate_badge_markdown(github, layout)
        readme_result = _inject_badges_into_readme(project_root, badge_md, write)
        badge_result = readme_result
        badge_result["markdown"] = badge_md
        badge_result["codeclimate_note"] = (
            "Replace PLACEHOLDER with your Code Climate badge token after "
            "connecting your repo at https://codeclimate.com"
        )
    else:
        badge_result["status"] = "skipped"
        badge_result["reason"] = "no_github_remote_detected"

    # --- SonarCloud scanner execution ---
    scanner_result: dict[str, Any] = {"status": "not_requested"}
    scanner_path = _detect_sonar_scanner()

    if sonar_token and write:
        if not scanner_path:
            scanner_result = {
                "status": "scanner_not_found",
                "install": "pip install pysonar-scanner",
                "note": "Install sonar-scanner to push analysis to SonarCloud.",
            }
        elif not os.path.exists(os.path.join(project_root, "sonar-project.properties")):
            scanner_result = {
                "status": "no_config",
                "note": "sonar-project.properties must exist before scanning.",
            }
        else:
            scanner_result = _run_sonar_scanner(
                project_root,
                sonar_token,
                scanner_path,
            )
    elif sonar_token and not write:
        scanner_result = {
            "status": "preview",
            "note": "Scanner will run when write=True. Token will be passed "
            "via SONAR_TOKEN env var (never written to disk).",
            "scanner_found": scanner_path is not None,
        }

    # --- Guidance ---
    guidance = _build_quality_guidance(github, layout, scanner_path)

    # --- Next actions ---
    owner = github.get("owner", "OWNER")
    repo = github.get("repo", "REPO")
    next_actions: list[dict[str, str]] = []

    files_to_stage: list[str] = []
    if cc_result.get("status") == "written":
        files_to_stage.append(".codeclimate.yml")
    if sonar_result.get("status") == "written":
        files_to_stage.append("sonar-project.properties")
    if coveragerc_result.get("status") == "written":
        files_to_stage.append(".coveragerc")
    if gitleaks_result.get("status") == "written":
        files_to_stage.append(".gitleaks.toml")
    if workflow_result.get("status") == "written":
        files_to_stage.append(".github/workflows/sonarcloud.yml")
    if tests_workflow_result.get("status") == "written":
        files_to_stage.append(".github/workflows/tests.yml")
    if qlty_workflow_result.get("status") == "written":
        files_to_stage.append(".github/workflows/qlty.yml")
    if security_workflow_result.get("status") == "written":
        files_to_stage.append(".github/workflows/security-lite.yml")
    if scorecard_result.get("status") in ("written", "drift_repaired"):
        files_to_stage.append(".github/workflows/scorecard.yml")
    if qi_gate_result.get("status") in ("written", "drift_repaired"):
        files_to_stage.append(".github/workflows/quality-infra-gate.yml")
    if dependabot_result.get("status") in ("written", "drift_repaired"):
        files_to_stage.append(".github/dependabot.yml")
    if security_md_result.get("status") == "written":
        files_to_stage.append("SECURITY.md")
    if pre_push_hook_result.get("status") == "written":
        files_to_stage.append(".githooks/pre-push")
    if qlty_result.get("status") == "written":
        files_to_stage.append(".qlty/qlty.toml")
    if qlty_result.get("gitignore_written"):
        files_to_stage.append(".qlty/.gitignore")
    if gi_result.get("status") in ("augmented", "created"):
        files_to_stage.append(".gitignore")
    if badge_result.get("status") in {"injected", "updated"}:
        files_to_stage.append("README.md")

    if files_to_stage:
        next_actions.append(
            {
                "tool": "Bash",
                "reason": "Stage and commit quality infrastructure",
                "example": (
                    f"git add {' '.join(files_to_stage)} && "
                    "git commit -m 'Add quality and security infrastructure (Code Climate + SonarCloud + qlty + security-lite)'"
                ),
            }
        )

    if github.get("detected"):
        if not sonar_token:
            next_actions.append(
                {
                    "tool": "Bash",
                    "reason": "Configure GitHub Actions secret required by SonarCloud workflow",
                    "example": (
                        f"gh secret set SONAR_TOKEN --repo {owner}/{repo} --body '<your_sonar_token>'"
                    ),
                }
            )
            next_actions.append(
                {
                    "tool": "setup_github_quality",
                    "reason": "Run sonar-scanner to push initial analysis and activate badge",
                    "example": (
                        f'setup_github_quality(path="{project_root}", '
                        'write=True, sonar_token="<your_token>")'
                    ),
                }
            )
        next_actions.append(
            {
                "tool": "Bash",
                "reason": "Enforce required checks for everyone (including admins) on main",
                "example": (
                    "gh api --method PUT "
                    f"/repos/{owner}/{repo}/branches/main/protection "
                    "--input - <<'JSON'\n"
                    "{\n"
                    '  "required_status_checks": {\n'
                    '    "strict": true,\n'
                    '    "contexts": [\n'
                    '      "Test Suite",\n'
                    '      "SonarQube Cloud Scan",\n'
                    '      "Secrets + SAST + Supply Chain",\n'
                    '      "Quality Infrastructure Gate"\n'
                    "    ]\n"
                    "  },\n"
                    '  "enforce_admins": true,\n'
                    '  "required_pull_request_reviews": null,\n'
                    '  "restrictions": null\n'
                    "}\n"
                    "JSON"
                ),
            }
        )
        next_actions.append(
            {
                "tool": "Bash",
                "reason": "Connect repo to Code Climate, then replace PLACEHOLDER in README badge",
                "example": f"open https://codeclimate.com/github/{owner}/{repo}",
            }
        )

    # Suggest qlty check if qlty is available
    qlty_path_bin = shutil.which("qlty")
    if qlty_path_bin:
        next_actions.append(
            {
                "tool": "Bash",
                "reason": "Run qlty local analysis for independent code quality check",
                "example": f"cd {project_root} && qlty check --all",
            }
        )

    output: dict[str, Any] = {
        "status": "written" if write else "preview",
        "github": github,
        "layout": layout,
        "codeclimate": cc_result,
        "sonar": sonar_result,
        "coveragerc": coveragerc_result,
        "gitleaks": gitleaks_result,
        "workflow": workflow_result,
        "tests_workflow": tests_workflow_result,
        "qlty_workflow": qlty_workflow_result,
        "security_workflow": security_workflow_result,
        "scorecard_workflow": scorecard_result,
        "quality_infra_gate_workflow": qi_gate_result,
        "dependabot": dependabot_result,
        "security_md": security_md_result,
        "pre_push_hook": pre_push_hook_result,
        "qlty": qlty_result,
        "gitignore": gi_result,
        "badges": badge_result,
        "scanner": scanner_result,
        "guidance": guidance,
        "next_actions": next_actions,
    }

    return json.dumps(output, indent=2)

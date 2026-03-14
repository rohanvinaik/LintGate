"""Onboarding tools — getting_started entry point for LintGate MCP.

Implementation lives in:
- _onboarding_venv_impl.py     (venv, install, tool-gap helpers)
- _onboarding_scaffold_impl.py (scaffold config, badges, project state reset)
- _onboarding_getting_started_impl.py (getting_started, applicability, setup_github_quality)
"""

from __future__ import annotations

import glob as glob_mod  # noqa: F401 — tests patch this namespace
import json  # noqa: F401 — tests patch this namespace
import os  # noqa: F401 — tests patch this namespace
import shlex  # noqa: F401 — tests patch this namespace
import shutil  # noqa: F401 — tests patch this namespace
import subprocess  # noqa: F401 — tests patch this namespace
import sys  # noqa: F401 — tests patch this namespace
from pathlib import Path  # noqa: F401, TC003 — tests patch this namespace
from typing import Any

from mcp_tools._onboarding_getting_started_impl import (  # noqa: F401
    _ESSENTIAL_TOOLS,
    _TOOL_APPLICABILITY_GUIDE,
    _build_next_actions,
    _detect_mutation_guard,
    _handle_config_and_venv,
    _handle_quality_bootstrap,
    _handle_tool_installs,
    _impl_getting_started,
    _impl_scaffold_config,
    _impl_setup_github_quality,
    _impl_tool_applicability_guide,
)
from mcp_tools._onboarding_scaffold_impl import (  # noqa: F401
    _CONTROLPLANE_YAML_BLOCK,
    _SCAFFOLD_EXCLUDED_SEGMENTS,
    _collect_python_files,
    _find_critical_paths,
    _has_subprocess_usage,
    _readme_has_quality_badges,
    _reset_project_state,
    _scaffold_config_yaml,
)
from mcp_tools._onboarding_venv_impl import (  # noqa: F401
    _OPTIONAL_STARTUP_PACKAGES,
    _auto_install_optional_tools,
    _collect_external_tool_gaps,
    _ensure_project_venv,
    _format_cmd,
    _install_command_for_package,
    _install_commands_for_package,
    _linter_available,
    _project_venv_python,
    _tool_package_name,
    _venv_create_command,
)
from mcp_tools.quality.discovery import (
    _parse_pyproject_metadata as _quality_parse_pyproject_metadata,
)
from mcp_tools.quality.discovery import (
    _scan_project_dirs as _quality_scan_project_dirs,
)
from mcp_tools.quality.rules_gen import (
    _normalize_qlty_exclude_pattern as _quality_normalize_qlty_exclude_pattern,
)
from mcp_tools.quality_helpers import (
    _BADGE_BLOCK_END,  # noqa: F401
    _BADGE_BLOCK_START,  # noqa: F401
    _README_NAMES,  # noqa: F401
    _REQUIRED_BADGE_FINGERPRINTS,  # noqa: F401
    _detect_github_remote,  # noqa: F401 — backward compat re-export
)
from mcp_tools.quality_helpers import (
    _compute_gitignore_additions as _quality_compute_gitignore_additions,
)
from mcp_tools.quality_helpers import (
    _detect_sonar_scanner as _quality_detect_sonar_scanner,
)
from mcp_tools.quality_helpers import (
    _detect_subprocess_usage as _quality_detect_subprocess_usage,
)
from mcp_tools.quality_helpers import (
    _generate_qlty_toml as _quality_generate_qlty_toml,
)
from mcp_tools.quality_helpers import (
    _inject_badges_into_readme as _quality_inject_badges_into_readme,
)
from mcp_tools.quality_helpers import (
    _run_sonar_scanner as _quality_run_sonar_scanner,
)
from mcp_tools.quality_helpers import (
    _write_pre_push_hook as _quality_write_pre_push_hook,
)

# ---------------------------------------------------------------------------
# Backward-compat wrappers for helpers moved to mcp_tools.quality_helpers.
# Tests import these from mcp_tools.onboarding_tools.
# ---------------------------------------------------------------------------


def _write_pre_push_hook(project_root: str, write: bool) -> dict[str, Any]:
    return _quality_write_pre_push_hook(project_root, write)


def _compute_gitignore_additions(project_root: str) -> dict[str, Any]:
    return _quality_compute_gitignore_additions(project_root)


def _inject_badges_into_readme(
    project_root: str,
    badge_markdown: str,
    write: bool,
) -> dict[str, Any]:
    return _quality_inject_badges_into_readme(
        project_root,
        badge_markdown,
        write=write,
    )


def _generate_qlty_toml(layout: dict[str, Any], *, is_tool_runner: bool = False) -> str:
    return _quality_generate_qlty_toml(layout, is_tool_runner=is_tool_runner)


def _normalize_qlty_exclude_pattern(pattern: str) -> str:
    return _quality_normalize_qlty_exclude_pattern(pattern)


def _detect_subprocess_usage(project_root: str) -> bool:
    return _quality_detect_subprocess_usage(project_root)


def _detect_sonar_scanner() -> str | None:
    return _quality_detect_sonar_scanner()


def _run_sonar_scanner(
    project_root: str,
    sonar_token: str,
    scanner_path: str,
) -> dict[str, Any]:
    return _quality_run_sonar_scanner(
        project_root,
        sonar_token,
        scanner_path,
    )


def _parse_pyproject_metadata(root: Path) -> tuple[str, str | None, list[str], bool]:
    return _quality_parse_pyproject_metadata(root)


def _scan_project_dirs(
    root: Path, test_dirs: list[str]
) -> tuple[list[str], list[str], list[str], str | None]:
    return _quality_scan_project_dirs(root, test_dirs)


# ---------------------------------------------------------------------------
# register() — thin @mcp.tool wrappers that delegate to _impl_* functions
# ---------------------------------------------------------------------------


def register(mcp, helpers):
    """Register onboarding tools on the shared MCP instance."""

    @mcp.tool()
    def getting_started(
        path: str,
        auto_setup: bool = True,
        auto_install_optional_linters: bool = True,
        reset: bool = False,
        intent: str | None = None,
    ) -> str:
        """Start here. Get oriented with LintGate on any project.

        WHEN TO USE: First tool call on any new project. Auto-detects IDE,
        creates venv if missing, installs optional linters (ruff, mypy, bandit,
        radon, pip-audit), initializes .claude/lintgate.yaml, and returns
        project-specific guidance with next-action recommendations.

        Workflow after getting_started:
          controlplane_run(path) → controlplane_get_details(run_id) →
          lint_fix(path) → bootstrap_context_files(path, write=True)

        For specification-first development:
          prescriptive_spec_compose(path, target) → prescriptive_spec_compile →
          [write code] → prescriptive_spec_verify

        For offline batch analysis:
          offline_analysis_generate(path) → upload notebook to Colab → run

        Args:
            path: Project root path.
            auto_setup: Create venv + install linters automatically (default True).
            auto_install_optional_linters: Install mypy/bandit/radon/pip-audit (default True).
            reset: Clear cached state and re-initialize (default False).
            intent: Optional hint like "fix lint", "improve tests", "full audit"
                to tailor next-action guidance.
        """
        return _impl_getting_started(
            helpers,
            path,
            auto_setup=auto_setup,
            auto_install_optional_linters=auto_install_optional_linters,
            reset=reset,
            intent=intent,
        )

    @mcp.tool()
    def tool_applicability_guide() -> str:
        """Returns the definitive guide on when and how to use each LintGate MCP tool.

        This covers cadence (how often to run), triggers (what events should prompt a run),
        and anti-patterns (when NOT to use the tool).
        """
        return _impl_tool_applicability_guide(helpers)

    @mcp.tool()
    def scaffold_config(path: str, write: bool = False) -> str:
        """Generate a project-specific lintgate.yaml from observed signals.

        WHEN TO USE: After running controlplane_run and reviewing findings.
        Analyzes the project to produce a tailored config with:
        - ControlPlane enabled with sensible channel defaults
        - Severity overrides for domain-expected bandit findings
        - Pipeline critical paths from file-too-long / CC hotspots
        - Inquiry features enabled

        Default mode is non-destructive (write=false) — returns the YAML
        for review. Set write=true to create .claude/lintgate.yaml.

        Example: scaffold_config(path="/my/project", write=True)
        """
        return _impl_scaffold_config(helpers, path, write=write)

    @mcp.tool()
    def setup_github_quality(
        path: str,
        write: bool = False,
        sonar_token: str | None = None,
    ) -> str:
        """Set up GitHub code quality badges and infrastructure for a project.

        WHEN TO USE: After getting_started when you want to add code quality
        badges and CI configuration to a project. Detects GitHub remote,
        project layout, and generates tailored configs for Code Climate,
        SonarCloud, qlty CLI, .gitignore augmentation, and README badge injection.

        Generates fifteen artifacts:
        - .codeclimate.yml — Code Climate / qlty Cloud config
        - sonar-project.properties — SonarCloud scanner config
        - .coveragerc — shared coverage scope for CI/Sonar workflows
        - .gitleaks.toml — gitleaks baseline config (extends defaults)
        - .github/workflows/sonarcloud.yml — SonarCloud analysis on push/PR
        - .github/workflows/qlty.yml — qlty analysis on push/PR
        - .github/workflows/security-lite.yml — secrets + SAST + supply-chain checks
        - .github/workflows/scorecard.yml — OpenSSF Scorecard analysis
        - .github/workflows/quality-infra-gate.yml — hard gate for infra completeness
        - .github/dependabot.yml — automated dependency updates
        - SECURITY.md — responsible disclosure policy
        - .qlty/qlty.toml — qlty analysis config with smart triage (commit to repo)
        - .githooks/pre-push — local git pre-push quality gate (with infra enforcement)
        - .gitignore augmentation — standard Python patterns
        - README badge injection — quality badges after title (8 badges + license)

        Default mode is non-destructive (write=false) — returns previews
        of all generated files. Set write=true to create/modify files.

        When sonar_token is provided with write=true, runs sonar-scanner
        to push initial analysis to SonarCloud (activates the badge).
        The token is passed via environment variable — never written to
        any file that could be committed.

        Example: setup_github_quality(path="/my/project", write=True)
        Example: setup_github_quality(path="/my/project", write=True,
                 sonar_token="your_token_here")
        """
        return _impl_setup_github_quality(helpers, path, write=write, sonar_token=sonar_token)

    return {
        "getting_started": getting_started,
        "scaffold_config": scaffold_config,
        "setup_github_quality": setup_github_quality,
        "tool_applicability_guide": tool_applicability_guide,
    }

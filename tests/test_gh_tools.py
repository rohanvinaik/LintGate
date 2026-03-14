"""Tests for mcp_tools/gh_tools.py — GitHub organization and wiki sync tools."""

from __future__ import annotations

import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

from mcp_tools._gh_helpers import (
    _detect_repo,
    _repo_full_name,
    _run_gh,
)
from mcp_tools._gh_organize_impl import (
    impl_project_organize_apply as _impl_project_organize_apply,
)
from mcp_tools._gh_organize_impl import (
    impl_project_organize_audit as _impl_project_organize_audit,
)
from mcp_tools._gh_wiki_impl import (
    _render_compass_wiki_page,
    _render_theory_wiki_page,
    _split_design_md,
)
from mcp_tools._gh_wiki_impl import (
    impl_project_wiki_read as _impl_project_wiki_read,
)
from mcp_tools._gh_wiki_impl import (
    impl_project_wiki_sync as _impl_project_wiki_sync,
)
from mcp_tools.gh_tools import register

# ── _run_gh ──────────────────────────────────────────────────────────


class TestRunGh:
    @patch("mcp_tools._gh_helpers.subprocess.run")
    def test_success_json(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='{"key": "value"}', stderr="")
        result = _run_gh(["api", "repos/owner/repo"])
        assert result == {"key": "value"}
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == ["gh", "api", "repos/owner/repo"]
        assert call_args[1]["timeout"] == 30

    @patch("mcp_tools._gh_helpers.subprocess.run")
    def test_success_empty_stdout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = _run_gh(["api", "test"])
        assert result == {}

    @patch("mcp_tools._gh_helpers.subprocess.run")
    def test_success_non_json_stdout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="plain text output", stderr="")
        result = _run_gh(["api", "test"])
        assert result == {"raw": "plain text output"}

    @patch("mcp_tools._gh_helpers.subprocess.run")
    def test_nonzero_returncode_stderr(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="some error")
        result = _run_gh(["api", "test"])
        assert result == {"error": "some error"}

    @patch("mcp_tools._gh_helpers.subprocess.run")
    def test_nonzero_returncode_no_stderr(self, mock_run):
        mock_run.return_value = MagicMock(returncode=42, stdout="", stderr="")
        result = _run_gh(["api", "test"])
        assert result == {"error": "gh exited with 42"}

    @patch("mcp_tools._gh_helpers.subprocess.run", side_effect=FileNotFoundError)
    def test_gh_not_found(self, mock_run):
        result = _run_gh(["api", "test"])
        assert "gh CLI not found" in result["error"]

    @patch(
        "mcp_tools._gh_helpers.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=30),
    )
    def test_timeout(self, mock_run):
        result = _run_gh(["api", "test"])
        assert "timed out" in result["error"]

    @patch("mcp_tools._gh_helpers.subprocess.run")
    def test_cwd_passed(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")
        _run_gh(["api", "test"], cwd="/some/path")
        assert mock_run.call_args[1]["cwd"] == "/some/path"


# ── _detect_repo ─────────────────────────────────────────────────────


class TestDetectRepo:
    @patch("mcp_tools._gh_helpers.subprocess.run")
    def test_detects_github_ssh_remote(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="origin\tgit@github.com:owner/repo.git (fetch)\norigin\tgit@github.com:owner/repo.git (push)\n",
            returncode=0,
        )
        result = _detect_repo("/project")
        assert result == {"owner": "owner", "repo": "repo"}

    @patch("mcp_tools._gh_helpers.subprocess.run")
    def test_detects_github_https_remote(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="origin\thttps://github.com/myorg/myrepo.git (fetch)\n",
            returncode=0,
        )
        result = _detect_repo("/project")
        assert result == {"owner": "myorg", "repo": "myrepo"}

    @patch("mcp_tools._gh_helpers.subprocess.run")
    def test_detects_https_no_git_suffix(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="origin\thttps://github.com/owner/repo (fetch)\n",
            returncode=0,
        )
        result = _detect_repo("/project")
        assert result == {"owner": "owner", "repo": "repo"}

    @patch(
        "mcp_tools._gh_helpers.subprocess.run",
        side_effect=subprocess.CalledProcessError(128, "git"),
    )
    def test_git_error_returns_empty(self, mock_run):
        result = _detect_repo("/project")
        assert result == {"owner": "", "repo": ""}

    @patch("mcp_tools._gh_helpers.subprocess.run", side_effect=FileNotFoundError)
    def test_git_not_found_returns_empty(self, mock_run):
        result = _detect_repo("/project")
        assert result == {"owner": "", "repo": ""}
        mock_run.assert_called_once()  # verify subprocess was attempted

    @patch("mcp_tools._gh_helpers.subprocess.run")
    def test_no_github_remote(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="origin\thttps://gitlab.com/owner/repo.git (fetch)\n",
            returncode=0,
        )
        result = _detect_repo("/project")
        assert result == {"owner": "", "repo": ""}


# ── _repo_full_name ──────────────────────────────────────────────────


class TestRepoFullName:
    @patch("mcp_tools._gh_helpers._detect_repo")
    def test_returns_owner_slash_repo(self, mock_detect):
        mock_detect.return_value = {"owner": "owner", "repo": "repo"}
        assert _repo_full_name("/project") == "owner/repo"

    @patch("mcp_tools._gh_helpers._detect_repo")
    def test_returns_empty_when_no_owner(self, mock_detect):
        mock_detect.return_value = {"owner": "", "repo": "repo"}
        assert _repo_full_name("/project") == ""

    @patch("mcp_tools._gh_helpers._detect_repo")
    def test_returns_empty_when_no_repo(self, mock_detect):
        mock_detect.return_value = {"owner": "owner", "repo": ""}
        assert _repo_full_name("/project") == ""

    @patch("mcp_tools._gh_helpers._detect_repo")
    def test_returns_empty_when_both_empty(self, mock_detect):
        mock_detect.return_value = {"owner": "", "repo": ""}
        assert _repo_full_name("/project") == ""


# ── _render_theory_wiki_page (pure) ──────────────────────────────────


class TestRenderTheoryWikiPage:
    def test_empty_facets(self):
        result = _render_theory_wiki_page({})
        assert "No theory profile extracted yet." in result
        assert "# Theory Profile" in result

    def test_empty_facets_key(self):
        result = _render_theory_wiki_page({"facets": {}})
        assert "No theory profile extracted yet." in result

    def test_facets_with_list_claims(self):
        theory = {
            "facets": {
                "core_theory": [
                    {"text": "claim one"},
                    {"text": "claim two"},
                ]
            }
        }
        result = _render_theory_wiki_page(theory)
        assert "## Core Theory" in result
        assert "- claim one" in result
        assert "- claim two" in result

    def test_facets_with_dict_claims(self):
        theory = {"facets": {"problem_solving": {"claims": [{"text": "solve via X"}]}}}
        result = _render_theory_wiki_page(theory)
        assert "## Problem Solving" in result
        assert "- solve via X" in result

    def test_facets_with_string_claims(self):
        theory = {"facets": {"alignment": ["align to quality", "align to speed"]}}
        result = _render_theory_wiki_page(theory)
        assert "- align to quality" in result
        assert "- align to speed" in result

    def test_facets_empty_claims(self):
        theory: dict[str, Any] = {"facets": {"core_theory": []}}
        result = _render_theory_wiki_page(theory)
        assert "_No claims extracted._" in result

    def test_claims_capped_at_20(self):
        claims = [{"text": f"claim {i}"} for i in range(30)]
        theory = {"facets": {"big_facet": claims}}
        result = _render_theory_wiki_page(theory)
        assert "- claim 19" in result
        assert "- claim 20" not in result

    def test_header_present(self):
        result = _render_theory_wiki_page({"facets": {"a": []}})
        assert "Auto-generated by LintGate" in result


# ── _render_compass_wiki_page (pure) ─────────────────────────────────


class TestRenderCompassWikiPage:
    def test_empty_axes(self):
        result = _render_compass_wiki_page({})
        assert "No compass data available yet." in result
        assert "# Project Compass" in result

    def test_empty_axes_key(self):
        result = _render_compass_wiki_page({"axes": {}})
        assert "No compass data available yet." in result

    def test_axis_with_all_sections(self):
        compass = {
            "axes": {
                "quality": {
                    "depth": 3,
                    "toward": ["high coverage"],
                    "away": ["tech debt"],
                    "forbidden": ["skip tests"],
                }
            }
        }
        result = _render_compass_wiki_page(compass)
        assert "## Quality" in result
        assert "**Depth**: 3" in result
        assert "### Toward" in result
        assert "- high coverage" in result
        assert "### Away" in result
        assert "- tech debt" in result
        assert "### Forbidden" in result
        assert "- skip tests" in result

    def test_axis_with_only_toward(self):
        compass = {
            "axes": {
                "perf": {
                    "depth": 2,
                    "toward": ["fast"],
                }
            }
        }
        result = _render_compass_wiki_page(compass)
        assert "## Perf" in result
        assert "### Toward" in result
        assert "- fast" in result
        assert "### Away" not in result
        assert "### Forbidden" not in result

    def test_axis_non_dict_value(self):
        compass = {"axes": {"simple": "just a string"}}
        result = _render_compass_wiki_page(compass)
        assert "## Simple" in result
        assert "just a string" in result

    def test_header_present(self):
        result = _render_compass_wiki_page({"axes": {"a": "b"}})
        assert "Auto-generated by LintGate" in result

    def test_underscore_in_axis_name_replaced(self):
        compass = {"axes": {"test_axis_name": {"depth": 1}}}
        result = _render_compass_wiki_page(compass)
        assert "## Test Axis Name" in result


# ── _split_design_md (pure) ──────────────────────────────────────────


class TestSplitDesignMd:
    def test_splits_sections_by_heading(self, tmp_path):
        content = "## First Section\n" + ("A" * 210) + "\n## Second Section\n" + ("B" * 210)
        (tmp_path / "design.md").write_text(content)
        pages = _split_design_md(str(tmp_path / "design.md"))
        assert "First-Section" in pages
        assert "Second-Section" in pages
        assert pages["First-Section"].startswith("# First Section")
        assert pages["Second-Section"].startswith("# Second Section")

    def test_skips_short_sections(self, tmp_path):
        content = "## Short\nToo short\n## Long\n" + ("X" * 210)
        (tmp_path / "design.md").write_text(content)
        pages = _split_design_md(str(tmp_path / "design.md"))
        assert "Short" not in pages
        assert "Long" in pages

    def test_nonexistent_file(self):
        pages = _split_design_md("/nonexistent/path/design.md")
        assert pages == {}

    def test_no_headings(self, tmp_path):
        content = "Just plain text without headings.\n" * 50
        (tmp_path / "design.md").write_text(content)
        pages = _split_design_md(str(tmp_path / "design.md"))
        assert pages == {}

    def test_heading_slug_special_chars(self, tmp_path):
        content = "## Hello World! (v2)\n" + ("Z" * 210)
        (tmp_path / "design.md").write_text(content)
        pages = _split_design_md(str(tmp_path / "design.md"))
        assert "Hello-World-v2" in pages

    def test_single_heading_enough_content(self, tmp_path):
        content = "## Only Section\n" + ("M" * 250)
        (tmp_path / "design.md").write_text(content)
        pages = _split_design_md(str(tmp_path / "design.md"))
        assert "Only-Section" in pages


# ── _impl_project_organize_audit ─────────────────────────────────────


class TestImplProjectOrganizeAudit:
    def _make_helpers(self):
        return {"_validate_project_root": lambda p: p}

    @patch("mcp_tools._gh_organize_impl._repo_full_name", return_value="")
    def test_no_repo_returns_error(self, mock_repo, tmp_path):
        (tmp_path / ".github" / "ISSUE_TEMPLATE").mkdir(parents=True)
        result = _impl_project_organize_audit(str(tmp_path), self._make_helpers())
        assert "error" in result
        assert "Could not detect GitHub remote" in result["error"]
        assert "next_actions" in result

    @patch("mcp_tools._gh_organize_impl._repo_full_name", return_value="")
    def test_missing_issue_templates(self, mock_repo, tmp_path):
        result = _impl_project_organize_audit(str(tmp_path), self._make_helpers())
        codes = [g["code"] for g in result["gaps"]]
        assert "ORG001" in codes

    @patch("mcp_tools._gh_organize_impl._repo_full_name", return_value="")
    def test_has_issue_templates_no_org001(self, mock_repo, tmp_path):
        (tmp_path / ".github" / "ISSUE_TEMPLATE").mkdir(parents=True)
        result = _impl_project_organize_audit(str(tmp_path), self._make_helpers())
        codes = [g["code"] for g in result["gaps"]]
        assert "ORG001" not in codes

    @patch("mcp_tools._gh_organize_impl._run_gh")
    @patch("mcp_tools._gh_organize_impl._repo_full_name", return_value="owner/repo")
    def test_missing_priority_labels_org002(self, mock_repo, mock_gh, tmp_path):
        (tmp_path / ".github" / "ISSUE_TEMPLATE").mkdir(parents=True)
        # First call: label list, second: issue list, third: milestones, fourth: repo info
        mock_gh.side_effect = [
            [{"name": "P0"}, {"name": "bug"}],  # labels - missing P1,P2,P3
            [],  # issues
            {"raw": "false"},  # repo has_wiki
        ]
        result = _impl_project_organize_audit(str(tmp_path), self._make_helpers())
        codes = [g["code"] for g in result["gaps"]]
        assert "ORG002" in codes


# ── _impl_project_organize_apply ─────────────────────────────────────


class TestImplProjectOrganizeApply:
    def _make_helpers(self):
        return {"_validate_project_root": lambda p: p}

    @patch("mcp_tools._gh_organize_impl._repo_full_name", return_value="")
    def test_no_repo_returns_error(self, mock_repo, tmp_path):
        result = _impl_project_organize_apply(str(tmp_path), None, False, self._make_helpers())
        assert result == {"error": "Could not detect GitHub remote."}

    @patch("mcp_tools._gh_organize_impl._run_gh")
    @patch("mcp_tools._gh_organize_impl._repo_full_name", return_value="owner/repo")
    def test_dry_run_plans_labels(self, mock_repo, mock_gh, tmp_path):
        mock_gh.return_value = []  # no existing labels
        result = _impl_project_organize_apply(
            str(tmp_path), ["labels"], False, self._make_helpers()
        )
        assert result["write"] is False
        assert result["planned_actions"] > 0
        assert result["applied_actions"] == 0

    @patch("mcp_tools._gh_organize_impl._run_gh")
    @patch("mcp_tools._gh_organize_impl._repo_full_name", return_value="owner/repo")
    def test_write_applies_labels(self, mock_repo, mock_gh, tmp_path):
        mock_gh.return_value = []  # no existing labels, then mock create calls
        result = _impl_project_organize_apply(str(tmp_path), ["labels"], True, self._make_helpers())
        assert result["write"] is True
        assert result["applied_actions"] > 0

    @patch("mcp_tools._gh_organize_impl._run_gh")
    @patch("mcp_tools._gh_organize_impl._repo_full_name", return_value="owner/repo")
    def test_existing_labels_not_recreated(self, mock_repo, mock_gh, tmp_path):
        existing = [{"name": f"P{i}"} for i in range(4)] + [
            {"name": f"type:{t}"} for t in ["research", "architecture", "bug", "refactor"]
        ]
        mock_gh.return_value = existing
        result = _impl_project_organize_apply(
            str(tmp_path), ["labels"], False, self._make_helpers()
        )
        assert result["planned_actions"] == 0


# ── _impl_project_wiki_sync ─────────────────────────────────────────


class TestImplProjectWikiSync:
    def _make_helpers(self):
        return {"_validate_project_root": lambda p: p}

    @patch("mcp_tools._gh_wiki_impl._repo_full_name", return_value="")
    def test_no_repo_returns_error(self, mock_repo, tmp_path):
        result = _impl_project_wiki_sync(str(tmp_path), "theory", False, self._make_helpers())
        assert result == {"error": "Could not detect GitHub remote."}

    @patch("mcp_tools._gh_wiki_impl._repo_full_name", return_value="owner/repo")
    def test_dry_run_theory(self, mock_repo, tmp_path):
        with (
            patch("mcp_tools._gh_wiki_impl._render_theory_wiki_page") as mock_render,
            patch("lintgate.theory_extractor.extract_theory", return_value={}),
        ):
            mock_render.return_value = "# Theory\ntest"
            result = _impl_project_wiki_sync(str(tmp_path), "theory", False, self._make_helpers())
        assert result["write"] is False
        assert "Theory-Profile" in result["page_names"]
        assert result["pages_generated"] >= 1

    @patch("mcp_tools._gh_wiki_impl._repo_full_name", return_value="owner/repo")
    def test_dry_run_compass_no_file(self, mock_repo, tmp_path):
        result = _impl_project_wiki_sync(str(tmp_path), "compass", False, self._make_helpers())
        assert result["write"] is False
        assert "Theory-Compass" in result["page_names"]


# ── _impl_project_wiki_read ─────────────────────────────────────────


class TestImplProjectWikiRead:
    def _make_helpers(self):
        return {"_validate_project_root": lambda p: p}

    @patch("mcp_tools._gh_wiki_impl._repo_full_name", return_value="")
    def test_no_repo_returns_error(self, mock_repo, tmp_path):
        result = _impl_project_wiki_read(str(tmp_path), "Home", self._make_helpers())
        assert result == {"error": "Could not detect GitHub remote."}

    @patch("mcp_tools._gh_wiki_impl._repo_full_name", return_value="owner/repo")
    def test_local_wiki_found(self, mock_repo, tmp_path):
        wiki_dir = tmp_path / ".lintgate" / "wiki"
        wiki_dir.mkdir(parents=True)
        (wiki_dir / "Home.md").write_text("# Home\nWelcome")
        result = _impl_project_wiki_read(str(tmp_path), "Home", self._make_helpers())
        assert result["source"] == "local"
        assert result["content"] == "# Home\nWelcome"
        assert result["page"] == "Home"

    @patch("mcp_tools._gh_wiki_impl._clone_wiki")
    @patch("mcp_tools._gh_wiki_impl._repo_full_name", return_value="owner/repo")
    def test_clone_error_returns_error(self, mock_repo, mock_clone, tmp_path):
        mock_clone.return_value = {"error": "Clone failed"}
        result = _impl_project_wiki_read(str(tmp_path), "Home", self._make_helpers())
        assert "error" in result
        assert "Clone failed" in result["error"]


# ── register ─────────────────────────────────────────────────────────


class TestRegister:
    def test_registers_four_tools(self):
        mcp = MagicMock()
        mcp.tool.return_value = lambda fn: fn
        helpers = {"_validate_project_root": lambda p: p}
        result = register(mcp, helpers)
        assert "project_organize_audit" in result
        assert "project_organize_apply" in result
        assert "project_wiki_sync" in result
        assert "project_wiki_read" in result
        assert len(result) == 4

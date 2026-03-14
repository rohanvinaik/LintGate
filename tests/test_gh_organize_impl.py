"""Tests for mcp_tools/_gh_organize_impl.py — per-check helpers and top-level impls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from mcp_tools._gh_organize_impl import (
    _check_issue_templates,
    _check_issues_missing_priority,
    _check_milestones,
    _check_priority_labels,
    _check_type_labels,
    _check_unlabeled_issues,
    _check_wiki_enabled,
    _fetch_issues,
    _fetch_labels,
    _parse_issues_list,
    _parse_label_names,
    _plan_missing_labels,
    impl_project_organize_apply,
    impl_project_organize_audit,
)


# ── _parse_label_names ─────────────────────────────────────────────────


class TestParseLabelNames:
    def test_list_input(self):
        labels = [{"name": "bug"}, {"name": "feature"}, {"name": ""}]
        assert _parse_label_names(labels) == ["bug", "feature", ""]

    def test_list_empty(self):
        assert _parse_label_names([]) == []

    def test_dict_with_raw_json(self):
        result = {"raw": json.dumps([{"name": "P0"}, {"name": "P1"}])}
        assert _parse_label_names(result) == ["P0", "P1"]

    def test_dict_with_error_returns_empty(self):
        result = {"error": "not found"}
        assert _parse_label_names(result) == []

    def test_dict_with_invalid_json_raw(self):
        result = {"raw": "not valid json"}
        assert _parse_label_names(result) == []

    def test_dict_with_empty_raw(self):
        result = {"raw": ""}
        assert _parse_label_names(result) == []

    def test_dict_raw_non_list_json(self):
        result = {"raw": json.dumps({"key": "value"})}
        assert _parse_label_names(result) == []

    def test_non_dict_non_list_returns_empty(self):
        assert _parse_label_names("string") == []
        assert _parse_label_names(42) == []
        assert _parse_label_names(None) == []

    def test_list_items_missing_name_key(self):
        labels = [{"id": 1}, {"name": "bug"}]
        assert _parse_label_names(labels) == ["", "bug"]


# ── _parse_issues_list ─────────────────────────────────────────────────


class TestParseIssuesList:
    def test_list_input_passthrough(self):
        issues = [{"number": 1}, {"number": 2}]
        assert _parse_issues_list(issues) == [{"number": 1}, {"number": 2}]

    def test_empty_list(self):
        assert _parse_issues_list([]) == []

    def test_dict_with_raw_json(self):
        raw_issues = [{"number": 10, "labels": []}]
        result = {"raw": json.dumps(raw_issues)}
        assert _parse_issues_list(result) == raw_issues

    def test_dict_with_invalid_json_raw(self):
        result = {"raw": "bad json"}
        assert _parse_issues_list(result) == []

    def test_dict_with_empty_raw(self):
        result = {"raw": ""}
        assert _parse_issues_list(result) == []

    def test_dict_raw_non_list(self):
        result = {"raw": json.dumps({"number": 1})}
        assert _parse_issues_list(result) == []

    def test_non_dict_non_list_returns_empty(self):
        assert _parse_issues_list("string") == []
        assert _parse_issues_list(None) == []
        assert _parse_issues_list(42) == []


# ── _check_issue_templates ─────────────────────────────────────────────


class TestCheckIssueTemplates:
    def test_no_template_dir(self, tmp_path):
        gaps = _check_issue_templates(str(tmp_path))
        assert len(gaps) == 1
        assert gaps[0]["code"] == "ORG001"
        assert gaps[0]["severity"] == "medium"
        assert "ISSUE_TEMPLATE" in gaps[0]["message"]

    def test_template_dir_exists(self, tmp_path):
        (tmp_path / ".github" / "ISSUE_TEMPLATE").mkdir(parents=True)
        gaps = _check_issue_templates(str(tmp_path))
        assert gaps == []

    def test_template_file_not_dir(self, tmp_path):
        """A file named ISSUE_TEMPLATE (not a directory) should still report."""
        gh = tmp_path / ".github"
        gh.mkdir()
        (gh / "ISSUE_TEMPLATE").write_text("not a dir")
        gaps = _check_issue_templates(str(tmp_path))
        assert len(gaps) == 1
        assert gaps[0]["code"] == "ORG001"


# ── _check_priority_labels ────────────────────────────────────────────


class TestCheckPriorityLabels:
    def test_all_present(self):
        labels = ["P0", "P1", "P2", "P3", "bug"]
        assert _check_priority_labels(labels) == []

    def test_missing_some(self):
        labels = ["P0", "P2"]
        gaps = _check_priority_labels(labels)
        assert len(gaps) == 1
        assert gaps[0]["code"] == "ORG002"
        assert gaps[0]["severity"] == "low"
        assert "P1" in gaps[0]["message"]
        assert "P3" in gaps[0]["message"]
        assert "P0" not in gaps[0]["message"]

    def test_none_present(self):
        gaps = _check_priority_labels(["bug", "feature"])
        assert len(gaps) == 1
        assert "P0" in gaps[0]["message"]
        assert "P1" in gaps[0]["message"]
        assert "P2" in gaps[0]["message"]
        assert "P3" in gaps[0]["message"]

    def test_similar_but_not_priority(self):
        """P4, P00, etc. should not count as priority labels."""
        gaps = _check_priority_labels(["P4", "P00", "priority"])
        assert len(gaps) == 1
        assert gaps[0]["code"] == "ORG002"


# ── _check_type_labels ────────────────────────────────────────────────


class TestCheckTypeLabels:
    def test_has_type_labels(self):
        labels = ["type:bug", "type:feature", "P0"]
        assert _check_type_labels(labels) == []

    def test_no_type_labels(self):
        labels = ["bug", "P0", "enhancement"]
        gaps = _check_type_labels(labels)
        assert len(gaps) == 1
        assert gaps[0]["code"] == "ORG003"
        assert gaps[0]["severity"] == "low"

    def test_empty_labels(self):
        gaps = _check_type_labels([])
        assert len(gaps) == 1
        assert gaps[0]["code"] == "ORG003"

    def test_partial_prefix_not_matching(self):
        """'types:' or 'Type:' should not match since startswith is case-sensitive."""
        gaps = _check_type_labels(["Type:bug", "types:feature"])
        assert len(gaps) == 1
        assert gaps[0]["code"] == "ORG003"


# ── _check_unlabeled_issues ──────────────────────────────────────────


class TestCheckUnlabeledIssues:
    def test_all_labeled(self):
        issues = [
            {"number": 1, "labels": [{"name": "bug"}]},
            {"number": 2, "labels": [{"name": "P0"}]},
        ]
        assert _check_unlabeled_issues(issues) == []

    def test_some_unlabeled(self):
        issues = [
            {"number": 1, "labels": [{"name": "bug"}]},
            {"number": 2, "labels": []},
            {"number": 3, "labels": None},
        ]
        gaps = _check_unlabeled_issues(issues)
        assert len(gaps) == 1
        assert gaps[0]["code"] == "ORG004"
        assert gaps[0]["severity"] == "medium"
        assert 2 in gaps[0]["issues"]
        assert 3 in gaps[0]["issues"]
        assert "2 issues" in gaps[0]["message"]

    def test_empty_issues_list(self):
        assert _check_unlabeled_issues([]) == []

    def test_truncation_at_20(self):
        issues = [{"number": i, "labels": []} for i in range(25)]
        gaps = _check_unlabeled_issues(issues)
        assert len(gaps[0]["issues"]) == 20


# ── _check_milestones ────────────────────────────────────────────────


class TestCheckMilestones:
    def test_few_issues_skips(self):
        """10 or fewer issues should return no findings."""
        assert _check_milestones("owner/repo", 10) == []
        assert _check_milestones("owner/repo", 0) == []

    @patch("mcp_tools._gh_organize_impl._run_gh")
    def test_no_milestones_reports(self, mock_run_gh):
        mock_run_gh.return_value = {"raw": "0"}
        gaps = _check_milestones("owner/repo", 15)
        assert len(gaps) == 1
        assert gaps[0]["code"] == "ORG005"
        assert gaps[0]["severity"] == "low"
        assert "15" in gaps[0]["message"]
        mock_run_gh.assert_called_once_with(
            ["api", "repos/owner/repo/milestones", "--jq", "length"],
        )

    @patch("mcp_tools._gh_organize_impl._run_gh")
    def test_has_milestones_no_report(self, mock_run_gh):
        mock_run_gh.return_value = {"raw": "3"}
        assert _check_milestones("owner/repo", 20) == []

    @patch("mcp_tools._gh_organize_impl._run_gh")
    def test_non_numeric_raw_treated_as_zero(self, mock_run_gh):
        mock_run_gh.return_value = {"raw": "abc"}
        gaps = _check_milestones("owner/repo", 11)
        assert len(gaps) == 1
        assert gaps[0]["code"] == "ORG005"

    @patch("mcp_tools._gh_organize_impl._run_gh")
    def test_non_dict_result_treated_as_zero(self, mock_run_gh):
        mock_run_gh.return_value = []
        gaps = _check_milestones("owner/repo", 11)
        assert len(gaps) == 1
        assert gaps[0]["code"] == "ORG005"


# ── _check_wiki_enabled ──────────────────────────────────────────────


class TestCheckWikiEnabled:
    @patch("mcp_tools._gh_organize_impl._run_gh")
    def test_wiki_false(self, mock_run_gh):
        mock_run_gh.return_value = {"raw": "false"}
        gaps = _check_wiki_enabled("owner/repo")
        assert len(gaps) == 1
        assert gaps[0]["code"] == "ORG006"
        assert gaps[0]["severity"] == "low"

    @patch("mcp_tools._gh_organize_impl._run_gh")
    def test_wiki_true(self, mock_run_gh):
        mock_run_gh.return_value = {"raw": "true"}
        assert _check_wiki_enabled("owner/repo") == []

    @patch("mcp_tools._gh_organize_impl._run_gh")
    def test_non_dict_result(self, mock_run_gh):
        mock_run_gh.return_value = []
        assert _check_wiki_enabled("owner/repo") == []

    @patch("mcp_tools._gh_organize_impl._run_gh")
    def test_wiki_false_with_whitespace(self, mock_run_gh):
        mock_run_gh.return_value = {"raw": "  False  \n"}
        gaps = _check_wiki_enabled("owner/repo")
        assert len(gaps) == 1
        assert gaps[0]["code"] == "ORG006"


# ── _check_issues_missing_priority ───────────────────────────────────


class TestCheckIssuesMissingPriority:
    def test_no_priority_labels_in_repo(self):
        """If repo has no P0-P3 labels, skip check entirely."""
        issues = [{"number": 1, "labels": [{"name": "bug"}]}]
        assert _check_issues_missing_priority(issues, ["bug"]) == []

    def test_no_issues(self):
        assert _check_issues_missing_priority([], ["P0", "P1", "P2", "P3"]) == []

    def test_all_have_priority(self):
        issues = [
            {"number": 1, "labels": [{"name": "P0"}]},
            {"number": 2, "labels": [{"name": "P2"}, {"name": "bug"}]},
        ]
        result = _check_issues_missing_priority(issues, ["P0", "P1", "P2", "P3"])
        assert result == []

    def test_some_missing_priority(self):
        issues = [
            {"number": 1, "labels": [{"name": "P0"}]},
            {"number": 2, "labels": [{"name": "bug"}]},
            {"number": 3, "labels": []},
        ]
        gaps = _check_issues_missing_priority(issues, ["P0", "P1", "P2", "P3"])
        assert len(gaps) == 1
        assert gaps[0]["code"] == "ORG007"
        assert gaps[0]["severity"] == "low"
        assert 2 in gaps[0]["issues"]
        assert 3 in gaps[0]["issues"]
        assert 1 not in gaps[0]["issues"]
        assert "2 issues" in gaps[0]["message"]

    def test_issues_with_none_labels(self):
        issues = [{"number": 5, "labels": None}]
        gaps = _check_issues_missing_priority(issues, ["P0", "P1", "P2", "P3"])
        assert len(gaps) == 1
        assert 5 in gaps[0]["issues"]

    def test_truncation_at_20(self):
        issues = [{"number": i, "labels": []} for i in range(30)]
        gaps = _check_issues_missing_priority(issues, ["P0", "P1", "P2", "P3"])
        assert len(gaps[0]["issues"]) == 20


# ── _fetch_labels ────────────────────────────────────────────────────


class TestFetchLabels:
    @patch("mcp_tools._gh_organize_impl._run_gh")
    def test_returns_parsed_names(self, mock_run_gh):
        mock_run_gh.return_value = [{"name": "P0"}, {"name": "bug"}]
        result = _fetch_labels("owner/repo")
        assert result == ["P0", "bug"]
        mock_run_gh.assert_called_once_with(
            ["label", "list", "--repo", "owner/repo", "--json", "name", "--limit", "200"],
        )

    @patch("mcp_tools._gh_organize_impl._run_gh")
    def test_empty_result(self, mock_run_gh):
        mock_run_gh.return_value = {"error": "not found"}
        assert _fetch_labels("owner/repo") == []


# ── _fetch_issues ────────────────────────────────────────────────────


class TestFetchIssues:
    @patch("mcp_tools._gh_organize_impl._run_gh")
    def test_returns_parsed_issues(self, mock_run_gh):
        issues = [{"number": 1, "labels": [{"name": "bug"}]}]
        mock_run_gh.return_value = issues
        result = _fetch_issues("owner/repo")
        assert result == issues
        args = mock_run_gh.call_args[0][0]
        assert "issue" in args
        assert "list" in args
        assert "--state" in args
        assert "open" in args

    @patch("mcp_tools._gh_organize_impl._run_gh")
    def test_error_returns_empty(self, mock_run_gh):
        mock_run_gh.return_value = {"error": "fail"}
        assert _fetch_issues("owner/repo") == []


# ── _plan_missing_labels ─────────────────────────────────────────────


class TestPlanMissingLabels:
    def test_all_exist_no_plan(self):
        existing = ["P0", "P1", "P2", "P3", "type:research", "type:architecture", "type:bug", "type:refactor"]
        planned, applied = _plan_missing_labels(existing, "owner/repo", False)
        assert planned == []
        assert applied == []

    def test_missing_labels_dry_run(self):
        existing = ["P0", "P1"]
        planned, applied = _plan_missing_labels(existing, "owner/repo", write=False)
        assert len(planned) > 0
        assert applied == []
        plan_names = [p["name"] for p in planned]
        assert "P2" in plan_names
        assert "P3" in plan_names
        assert "P0" not in plan_names
        assert "P1" not in plan_names
        for action in planned:
            assert action["type"] == "create_label"
            assert "color" in action

    @patch("mcp_tools._gh_organize_impl._run_gh")
    def test_missing_labels_write_mode(self, mock_run_gh):
        mock_run_gh.return_value = {"ok": True}
        existing = ["P0", "P1", "P2", "P3", "type:research", "type:architecture", "type:bug"]
        planned, applied = _plan_missing_labels(existing, "owner/repo", write=True)
        assert len(planned) == 1
        assert len(applied) == 1
        assert applied[0]["name"] == "type:refactor"
        assert "result" in applied[0]
        mock_run_gh.assert_called_once()

    def test_priority_labels_have_description(self):
        """When writing, priority labels should include --description flag."""
        with patch("mcp_tools._gh_organize_impl._run_gh") as mock_run_gh:
            mock_run_gh.return_value = {"ok": True}
            existing = ["P0", "P1", "P2", "type:research", "type:architecture", "type:bug", "type:refactor"]
            _plan_missing_labels(existing, "owner/repo", write=True)
            call_args = mock_run_gh.call_args[0][0]
            assert "--description" in call_args
            assert "P3" in call_args[2]  # label name is the 3rd arg


# ── impl_project_organize_audit ──────────────────────────────────────


class TestImplProjectOrganizeAudit:
    def _helpers(self) -> dict[str, Any]:
        return {"_validate_project_root": MagicMock()}

    @patch("mcp_tools._gh_organize_impl._repo_full_name", return_value="")
    def test_no_remote(self, mock_rfn, tmp_path):
        result = impl_project_organize_audit(str(tmp_path), self._helpers())
        assert result["repo"] is None
        assert "error" in result
        assert "Could not detect GitHub remote" in result["error"]
        assert "next_actions" in result
        # ORG001 should still fire (no template dir)
        assert any(g["code"] == "ORG001" for g in result["gaps"])

    @patch("mcp_tools._gh_organize_impl._check_wiki_enabled", return_value=[])
    @patch("mcp_tools._gh_organize_impl._check_milestones", return_value=[])
    @patch("mcp_tools._gh_organize_impl._fetch_issues", return_value=[])
    @patch("mcp_tools._gh_organize_impl._fetch_labels", return_value=["P0", "P1", "P2", "P3", "type:bug"])
    @patch("mcp_tools._gh_organize_impl._repo_full_name", return_value="owner/repo")
    def test_full_audit_clean(self, _rfn, _fl, _fi, _cm, _cw, tmp_path):
        (tmp_path / ".github" / "ISSUE_TEMPLATE").mkdir(parents=True)
        result = impl_project_organize_audit(str(tmp_path), self._helpers())
        assert result["repo"] == "owner/repo"
        assert result["total_gaps"] == 0
        assert result["gaps"] == []
        assert result["labels_found"] == 5
        assert result["open_issues"] == 0
        assert "next_actions" in result

    @patch("mcp_tools._gh_organize_impl._check_wiki_enabled", return_value=[])
    @patch("mcp_tools._gh_organize_impl._check_milestones", return_value=[])
    @patch("mcp_tools._gh_organize_impl._fetch_issues")
    @patch("mcp_tools._gh_organize_impl._fetch_labels", return_value=[])
    @patch("mcp_tools._gh_organize_impl._repo_full_name", return_value="owner/repo")
    def test_audit_detects_missing_labels(self, _rfn, _fl, _fi, _cm, _cw, tmp_path):
        (tmp_path / ".github" / "ISSUE_TEMPLATE").mkdir(parents=True)
        _fi.return_value = []
        result = impl_project_organize_audit(str(tmp_path), self._helpers())
        codes = [g["code"] for g in result["gaps"]]
        assert "ORG002" in codes  # missing priority labels
        assert "ORG003" in codes  # missing type labels
        assert result["total_gaps"] >= 2


# ── impl_project_organize_apply ──────────────────────────────────────


class TestImplProjectOrganizeApply:
    def _helpers(self) -> dict[str, Any]:
        return {"_validate_project_root": MagicMock()}

    @patch("mcp_tools._gh_organize_impl._repo_full_name", return_value="")
    def test_no_remote(self, mock_rfn):
        result = impl_project_organize_apply("/tmp/test", None, False, self._helpers())
        assert "error" in result
        assert "Could not detect GitHub remote" in result["error"]

    @patch("mcp_tools._gh_organize_impl._plan_missing_labels")
    @patch("mcp_tools._gh_organize_impl._fetch_labels", return_value=["P0"])
    @patch("mcp_tools._gh_organize_impl._repo_full_name", return_value="owner/repo")
    def test_dry_run_labels(self, _rfn, _fl, mock_plan):
        planned = [{"type": "create_label", "name": "P1", "color": "D93F0B"}]
        mock_plan.return_value = (planned, [])
        result = impl_project_organize_apply("/tmp/test", None, False, self._helpers())
        assert result["write"] is False
        assert result["planned_actions"] == 1
        assert result["applied_actions"] == 0
        assert result["actions"] == planned
        assert result["repo"] == "owner/repo"

    @patch("mcp_tools._gh_organize_impl._plan_missing_labels")
    @patch("mcp_tools._gh_organize_impl._fetch_labels", return_value=["P0"])
    @patch("mcp_tools._gh_organize_impl._repo_full_name", return_value="owner/repo")
    def test_write_mode_labels(self, _rfn, _fl, mock_plan):
        applied_action = {"type": "create_label", "name": "P1", "color": "D93F0B", "result": {"ok": True}}
        mock_plan.return_value = ([applied_action], [applied_action])
        result = impl_project_organize_apply("/tmp/test", ["labels"], True, self._helpers())
        assert result["write"] is True
        assert result["applied_actions"] == 1
        assert result["actions"] == [applied_action]

    @patch("mcp_tools._gh_organize_impl._repo_full_name", return_value="owner/repo")
    def test_default_actions_include_labels(self, _rfn):
        """When actions=None, defaults to ['labels', 'milestones']."""
        with patch("mcp_tools._gh_organize_impl._fetch_labels", return_value=[]) as fl, \
             patch("mcp_tools._gh_organize_impl._plan_missing_labels", return_value=([], [])):
            impl_project_organize_apply("/tmp/test", None, False, self._helpers())
            fl.assert_called_once()

    @patch("mcp_tools._gh_organize_impl._repo_full_name", return_value="owner/repo")
    def test_actions_without_labels_skips_fetch(self, _rfn):
        """When 'labels' not in actions list, skip label fetch."""
        result = impl_project_organize_apply("/tmp/test", ["milestones"], False, self._helpers())
        assert result["planned_actions"] == 0

"""Tests for lintgate/channels/_git_helpers.py."""

from __future__ import annotations

from unittest.mock import patch

from lintgate.channels._git_helpers import (
    _collect_branch_name,
    _collect_file_status,
    _collect_loc_delta,
    _is_git_repo,
    _parse_diff_stat_totals,
    classify_finding_scope,
    collect_working_tree_context,
)

# ── _parse_diff_stat_totals ─────────────────────────────────────────────


def test_parse_diff_stat_totals_both():
    output = " 3 files changed, 42 insertions(+), 7 deletions(-)\n"
    assert _parse_diff_stat_totals(output) == (42, 7)


def test_parse_diff_stat_totals_insertions_only():
    output = " 1 file changed, 10 insertions(+)\n"
    assert _parse_diff_stat_totals(output) == (10, 0)


def test_parse_diff_stat_totals_deletions_only():
    output = " 1 file changed, 5 deletions(-)\n"
    assert _parse_diff_stat_totals(output) == (0, 5)


def test_parse_diff_stat_totals_empty():
    assert _parse_diff_stat_totals("") == (0, 0)


def test_parse_diff_stat_totals_no_summary():
    output = " foo.py | 3 +++\n"
    assert _parse_diff_stat_totals(output) == (0, 0)


# ── _is_git_repo ────────────────────────────────────────────────────────


def test_is_git_repo_with_dot_git(tmp_path):
    (tmp_path / ".git").mkdir()
    assert _is_git_repo(str(tmp_path)) is True


def test_is_git_repo_no_git(tmp_path):
    # tmp_path has no .git; run_cmd fallback will also fail
    with patch("lintgate.channels._git_helpers.run_cmd", return_value=None):
        assert _is_git_repo(str(tmp_path)) is False


# ── _collect_branch_name ────────────────────────────────────────────────


def test_collect_branch_name_success():
    class FakeResult:
        stdout = "  feature/abc  \n"

    with patch("lintgate.channels._git_helpers.run_cmd", return_value=FakeResult()):
        assert _collect_branch_name("/repo") == "feature/abc"


def test_collect_branch_name_failure():
    with patch("lintgate.channels._git_helpers.run_cmd", return_value=None):
        assert _collect_branch_name("/repo") == ""


# ── _collect_file_status ────────────────────────────────────────────────


def test_collect_file_status_mixed():
    class FakeResult:
        stdout = " M src/foo.py\n?? new_file.py\nMM bar.py\n"

    with patch("lintgate.channels._git_helpers.run_cmd", return_value=FakeResult()):
        modified, untracked = _collect_file_status("/repo")
    assert modified == ["src/foo.py", "bar.py"]
    assert untracked == ["new_file.py"]


def test_collect_file_status_empty():
    class FakeResult:
        stdout = ""

    with patch("lintgate.channels._git_helpers.run_cmd", return_value=FakeResult()):
        modified, untracked = _collect_file_status("/repo")
    assert modified == []
    assert untracked == []


def test_collect_file_status_failure():
    with patch("lintgate.channels._git_helpers.run_cmd", return_value=None):
        assert _collect_file_status("/repo") == ([], [])


# ── _collect_loc_delta ──────────────────────────────────────────────────


def test_collect_loc_delta_success():
    class FakeResult:
        stdout = " 2 files changed, 20 insertions(+), 3 deletions(-)\n"

    with patch("lintgate.channels._git_helpers.run_cmd", return_value=FakeResult()):
        assert _collect_loc_delta("/repo") == (20, 3)


def test_collect_loc_delta_failure():
    with patch("lintgate.channels._git_helpers.run_cmd", return_value=None):
        assert _collect_loc_delta("/repo") == (0, 0)


# ── classify_finding_scope ──────────────────────────────────────────────


def test_classify_finding_scope_unknown_no_file():
    assert classify_finding_scope(None, [], [], "/project") == "unknown"


def test_classify_finding_scope_new_file():
    assert classify_finding_scope("new.py", [], ["new.py"], "/project") == "new_file"


def test_classify_finding_scope_uncommitted():
    assert classify_finding_scope("src/foo.py", ["src/foo.py"], [], "/project") == "uncommitted"


def test_classify_finding_scope_committed():
    assert classify_finding_scope("src/foo.py", [], [], "/project") == "committed"


def test_classify_finding_scope_absolute_path():
    result = classify_finding_scope(
        "/project/src/foo.py",
        ["src/foo.py"],
        [],
        "/project",
    )
    assert result == "uncommitted"


# ── collect_working_tree_context ─────────────────────────────────────────


def test_collect_working_tree_context_not_git(tmp_path):
    with patch("lintgate.channels._git_helpers.run_cmd", return_value=None):
        ctx = collect_working_tree_context(str(tmp_path))
    assert ctx["branch"] == ""
    assert ctx["modified_files"] == []
    assert ctx["large_uncommitted_diff"] is False


def test_collect_working_tree_context_git(tmp_path):
    (tmp_path / ".git").mkdir()

    branch_result = type("R", (), {"stdout": "main\n"})()
    status_result = type("R", (), {"stdout": " M a.py\n?? b.py\n"})()
    diff_result = type("R", (), {"stdout": " 1 file changed, 5 insertions(+), 2 deletions(-)\n"})()

    call_count = 0

    def fake_run_cmd(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        if "rev-parse" in cmd and "--abbrev-ref" in cmd:
            return branch_result
        if "status" in cmd:
            return status_result
        if "diff" in cmd:
            return diff_result
        return None

    with patch("lintgate.channels._git_helpers.run_cmd", side_effect=fake_run_cmd):
        ctx = collect_working_tree_context(str(tmp_path))

    assert ctx["branch"] == "main"
    assert ctx["modified_count"] == 1
    assert ctx["untracked_count"] == 1
    assert ctx["uncommitted_loc_delta"] == 3  # 5 - 2
    assert ctx["large_uncommitted_diff"] is False

"""Coverage-focused tests for scripts/ship_main.py."""

from __future__ import annotations

import importlib.util
import stat
import subprocess
import sys
from pathlib import Path

import pytest


def _load_ship_main():
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "ship_main.py"
    spec = importlib.util.spec_from_file_location("ship_main", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load scripts/ship_main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ship_main():
    return _load_ship_main()


def test_run_wrapper_calls_subprocess(ship_main, monkeypatch):
    calls = {}

    def fake_run(cmd, cwd, check, text, capture_output):
        calls["cmd"] = cmd
        calls["cwd"] = cwd
        calls["check"] = check
        calls["text"] = text
        calls["capture_output"] = capture_output
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(ship_main.subprocess, "run", fake_run)
    result = ship_main._run(["echo", "ok"], cwd="/tmp", capture=True)

    assert result.returncode == 0
    assert calls["cmd"] == ["echo", "ok"]
    assert calls["cwd"] == "/tmp"
    assert calls["check"] is True
    assert calls["text"] is True
    assert calls["capture_output"] is True


def test_require_tool_success_and_failure(ship_main, monkeypatch):
    monkeypatch.setattr(ship_main.shutil, "which", lambda _: "/usr/bin/gh")
    ship_main._require_tool("gh", "/repo")

    monkeypatch.setattr(ship_main.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="Missing required tool: gh"):
        ship_main._require_tool("gh", "/repo")


def test_git_output_and_gh_json(ship_main, monkeypatch):
    def fake_run(*args, **kwargs):
        if args[0][0] == "gh":
            return subprocess.CompletedProcess(args[0], 0, stdout='{"x": 1}', stderr="")
        return subprocess.CompletedProcess(args[0], 0, stdout="  value  \n", stderr="")

    monkeypatch.setattr(ship_main, "_run", fake_run)

    assert ship_main._git_output("/repo", "status", "--porcelain") == "value"
    assert ship_main._gh_json("/repo", "repo", "view") == {"x": 1}


def test_require_clean_worktree(ship_main, monkeypatch):
    monkeypatch.setattr(ship_main, "_git_output", lambda *_: "")
    ship_main._require_clean_worktree("/repo")

    monkeypatch.setattr(ship_main, "_git_output", lambda *_: " M lintgate/foo.py")
    with pytest.raises(RuntimeError, match="Tracked changes detected"):
        ship_main._require_clean_worktree("/repo")


def test_ensure_branch_keeps_side_branch(ship_main, monkeypatch):
    monkeypatch.setattr(ship_main, "_git_output", lambda *_: "codex/work")
    assert ship_main._ensure_branch("/repo", "main") == "codex/work"


def test_ensure_branch_creates_ephemeral_from_base(ship_main, monkeypatch):
    calls: list[list[str]] = []

    monkeypatch.setattr(ship_main, "_git_output", lambda *_: "main")
    monkeypatch.setattr(ship_main, "_run", lambda cmd, **_: calls.append(cmd))

    branch = ship_main._ensure_branch("/repo", "main")

    assert branch.startswith("codex/ship-")
    assert calls and calls[0][:3] == ["git", "switch", "-c"]


def test_ensure_branch_rejects_detached_head(ship_main, monkeypatch):
    monkeypatch.setattr(ship_main, "_git_output", lambda *_: "")
    with pytest.raises(RuntimeError, match="Detached HEAD"):
        ship_main._ensure_branch("/repo", "main")


def test_run_local_gate_stack_validates_and_executes(ship_main, tmp_path, monkeypatch):
    repo = tmp_path
    hook = repo / ".githooks" / "pre-push"

    with pytest.raises(RuntimeError, match="Missing .githooks/pre-push"):
        ship_main._run_local_gate_stack(str(repo))

    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 0\n")
    with pytest.raises(RuntimeError, match="not executable"):
        ship_main._run_local_gate_stack(str(repo))

    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["cwd"] = kwargs["cwd"]
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ship_main, "_run", fake_run)
    ship_main._run_local_gate_stack(str(repo))
    assert calls["cmd"] == [str(hook)]
    assert calls["cwd"] == str(repo)


def test_push_branch(ship_main, monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["cwd"] = kwargs["cwd"]
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ship_main, "_run", fake_run)
    ship_main._push_branch("/repo", "origin", "codex/ship-a")
    assert seen["cmd"] == ["git", "push", "-u", "origin", "codex/ship-a"]
    assert seen["cwd"] == "/repo"


def test_resolve_pr_reuses_existing(ship_main, monkeypatch):
    monkeypatch.setattr(
        ship_main,
        "_gh_json",
        lambda *_: [{"number": 123, "url": "https://example/pr/123"}],
    )
    number, url = ship_main._resolve_pr("/repo", "codex/x", "main")
    assert (number, url) == (123, "https://example/pr/123")


def test_resolve_pr_creates_when_missing(ship_main, monkeypatch):
    calls: list[list[str]] = []
    gh_json_calls = {"count": 0}

    def fake_gh_json(*_args):
        gh_json_calls["count"] += 1
        if gh_json_calls["count"] == 1:
            return []
        return {"number": 77, "url": "https://example/pr/77"}

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ship_main, "_gh_json", fake_gh_json)
    monkeypatch.setattr(ship_main, "_run", fake_run)
    number, url = ship_main._resolve_pr("/repo", "codex/x", "main")
    assert (number, url) == (77, "https://example/pr/77")
    assert any(cmd[:3] == ["gh", "pr", "create"] for cmd in calls)


# Tests for _repo_slug, _required_checks_from_branch_protection,
# _required_checks_from_contract, _union_checks, _read_check_runs,
# _watch_required_checks, and _merge_pr were removed: these functions
# were intentionally deleted when CI monitoring was delegated to
# lintgate[bot] (Cloudflare Worker). See scripts/ship_main.py docstring.


def test_prune_merged_local_branches(ship_main, monkeypatch):
    deleted: list[str] = []

    def fake_git_output(_repo, *_args):
        return "\n".join(["main", "codex/current", "codex/old", "feat/x", "random"])

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            branch = cmd[3]
            returncode = 0 if branch in {"codex/old", "feat/x"} else 1
            return subprocess.CompletedProcess(cmd, returncode=returncode, stdout="", stderr="")
        if cmd[:2] == ["git", "branch"]:
            deleted.append(cmd[3])
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(ship_main, "_git_output", fake_git_output)
    monkeypatch.setattr(ship_main.subprocess, "run", fake_run)

    ship_main._prune_merged_local_branches("/repo", "main", "codex/current")
    assert set(deleted) == {"codex/old", "feat/x"}


# test_main_no_merge_flow, test_main_raises_when_no_required_checks,
# test_main_merge_and_prune_flow removed: these tested the old CI-watching
# flow that was intentionally replaced by lintgate[bot] delegation.


def test_main_auth_failure(ship_main, monkeypatch):
    repo_root = "/tmp/repo"

    def fake_git_output(_repo, *args):
        if args == ("rev-parse", "--show-toplevel"):
            return repo_root
        raise AssertionError(args)

    monkeypatch.setattr(ship_main, "_git_output", fake_git_output)
    monkeypatch.setattr(
        ship_main.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["gh"], returncode=1, stdout="", stderr=""
        ),
    )
    monkeypatch.setattr(ship_main, "_require_tool", lambda *_: None)

    old_argv = sys.argv
    sys.argv = ["ship_main.py"]
    try:
        with pytest.raises(RuntimeError, match="gh auth is not configured"):
            ship_main.main()
    finally:
        sys.argv = old_argv


def test_main_push_and_prune_flow(ship_main, monkeypatch):
    """Test the current simplified flow: gates → push → PR → done."""
    repo_root = "/tmp/repo"
    seen = {"pruned": False}

    def fake_git_output(_repo, *args):
        if args == ("rev-parse", "--show-toplevel"):
            return repo_root
        raise AssertionError(args)

    monkeypatch.setattr(ship_main, "_git_output", fake_git_output)
    monkeypatch.setattr(
        ship_main.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["gh"], returncode=0, stdout="", stderr=""
        ),
    )
    monkeypatch.setattr(ship_main, "_require_tool", lambda *_: None)
    monkeypatch.setattr(ship_main, "_require_clean_worktree", lambda *_: None)
    monkeypatch.setattr(ship_main, "_ensure_branch", lambda *_: "codex/ship-test")
    monkeypatch.setattr(
        ship_main, "_run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0)
    )
    monkeypatch.setattr(ship_main, "_check_mergeability", lambda *_: (True, "clean"))
    monkeypatch.setattr(ship_main, "_run_local_gate_stack", lambda *_: None)
    monkeypatch.setattr(ship_main, "_push_branch", lambda *_: None)
    monkeypatch.setattr(ship_main, "_resolve_pr", lambda *_: (7, "https://example/pr/7"))
    monkeypatch.setattr(
        ship_main,
        "_prune_merged_local_branches",
        lambda *_: seen.__setitem__("pruned", True),
    )

    old_argv = sys.argv
    sys.argv = ["ship_main.py", "--prune-merged"]
    try:
        assert ship_main.main() == 0
    finally:
        sys.argv = old_argv

    assert seen["pruned"] is True


def test_file_level_runtime_error_print(monkeypatch, capsys):
    ship_main = _load_ship_main()
    monkeypatch.setattr(ship_main, "main", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(SystemExit):
        try:
            raise SystemExit(ship_main.main())
        except RuntimeError as exc:
            print(f"[ship] ERROR: {exc}")
            raise SystemExit(1) from exc

    out = capsys.readouterr().out
    assert "[ship] ERROR: boom" in out


def test_main_preflight_flow(ship_main, monkeypatch):
    repo_root = "/tmp/repo"
    seen = {"preflight_called": False, "push_called": False}

    def fake_git_output(_repo, *args):
        if args == ("rev-parse", "--show-toplevel"):
            return repo_root
        raise AssertionError(args)

    def fake_run_preflight(repo, json_mode):
        assert repo == repo_root
        assert json_mode is False
        seen["preflight_called"] = True
        return 0

    def fake_push_branch(*args, **kwargs):
        seen["push_called"] = True

    monkeypatch.setattr(ship_main, "_git_output", fake_git_output)
    monkeypatch.setattr(ship_main, "_run_preflight", fake_run_preflight)
    monkeypatch.setattr(ship_main, "_push_branch", fake_push_branch)

    old_argv = sys.argv
    sys.argv = ["ship_main.py", "--preflight"]
    try:
        assert ship_main.main() == 0
    finally:
        sys.argv = old_argv

    assert seen["preflight_called"] is True
    assert seen["push_called"] is False


def test_run_preflight_non_json_missing_hook_raises(ship_main, tmp_path):
    with pytest.raises(RuntimeError, match="Missing .githooks/pre-push"):
        ship_main._run_preflight(str(tmp_path), json_mode=False)


def test_run_preflight_non_json_non_executable_raises(ship_main, tmp_path):
    hook = tmp_path / ".githooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="not executable"):
        ship_main._run_preflight(str(tmp_path), json_mode=False)


def test_run_preflight_json_parses_failed_gate_ids(ship_main, monkeypatch, tmp_path, capsys):
    hook = tmp_path / ".githooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

    stdout = (
        "[lintgate][gate:symbol_gate] BLOCKED: symbol gate failed\n"
        "[lintgate][gate:quality_infra] BLOCKED: quality infrastructure incomplete\n"
        "[lintgate][gate:tests] FAIL: pytest failed\n"
        "[lintgate][gate:sonar] BLOCKED: sonar fail\n"
    )
    monkeypatch.setattr(
        ship_main.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["hook"], 1, stdout=stdout, stderr=""
        ),
    )

    code = ship_main._run_preflight(str(tmp_path), json_mode=True)
    payload = ship_main.json.loads(capsys.readouterr().out.strip())
    assert code == 1
    assert payload["status"] == "fail"
    assert payload["failed_gate_ids"] == [
        "symbol_gate",
        "quality_infra",
        "tests",
        "sonar",
    ]


def test_run_preflight_json_uses_fallback_gate_id(ship_main, monkeypatch, tmp_path, capsys):
    hook = tmp_path / ".githooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setattr(
        ship_main.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["hook"], 1, stdout="unknown failure", stderr=""
        ),
    )

    code = ship_main._run_preflight(str(tmp_path), json_mode=True)
    payload = ship_main.json.loads(capsys.readouterr().out.strip())
    assert code == 1
    assert payload["failed_gate_ids"] == ["pre-push-hook"]


def test_main_preflight_json_requires_preflight(ship_main, monkeypatch):
    old_argv = sys.argv
    sys.argv = ["ship_main.py", "--json"]
    try:
        with pytest.raises(RuntimeError, match="--json can only be used with --preflight"):
            ship_main.main()
    finally:
        sys.argv = old_argv


def test_run_preflight_json_output(ship_main, monkeypatch, tmp_path, capsys):
    import json

    repo = tmp_path
    hook = repo / ".githooks" / "pre-push"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/bin/sh\\nexit 0\\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

    def fake_run(cmd, **kwargs):
        class FakeProc:
            returncode = 1
            stdout = "[lintgate] BLOCKED: secrets detected\\n"
            stderr = ""

        return FakeProc()

    monkeypatch.setattr(ship_main.subprocess, "run", fake_run)

    code = ship_main._run_preflight(str(repo), json_mode=True)
    assert code == 1

    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["status"] == "fail"
    assert data["exit_code"] == 1
    assert "gitleaks" in data["failed_gate_ids"]


def test_run_preflight_json_missing_hook_emits_error(ship_main, tmp_path, capsys):
    import json

    code = ship_main._run_preflight(str(tmp_path), json_mode=True)
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "error"
    assert data["error"] == "Missing .githooks/pre-push"


def test_run_preflight_json_non_executable_hook_emits_error(ship_main, tmp_path, capsys):
    import json

    hook = tmp_path / ".githooks" / "pre-push"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/bin/sh\nexit 0\n")
    # intentionally do not chmod +x

    code = ship_main._run_preflight(str(tmp_path), json_mode=True)
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "error"
    assert data["error"] == ".githooks/pre-push is not executable"


def test_run_preflight_non_json_prints_banner(ship_main, monkeypatch, tmp_path, capsys):
    hook = tmp_path / ".githooks" / "pre-push"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ship_main.subprocess, "run", fake_run)
    code = ship_main._run_preflight(str(tmp_path), json_mode=False)
    assert code == 0
    assert "[ship] [PREFLIGHT] Running strict local gate stack" in capsys.readouterr().out


# ── _check_mergeability ──────────────────────────────────────────────


def test_check_mergeability_clean(ship_main, monkeypatch):
    """Merge-tree returns 0 → mergeable."""
    monkeypatch.setattr(
        ship_main, "_run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0)
    )
    monkeypatch.setattr(ship_main, "_git_output", lambda *_: "abc123")
    monkeypatch.setattr(
        ship_main.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="", stderr=""),
    )
    ok, detail = ship_main._check_mergeability("/repo", "feat/x", "main", "origin")
    assert ok is True
    assert "cleanly" in detail


def test_check_mergeability_conflict(ship_main, monkeypatch):
    """Merge-tree returns non-zero with CONFLICT lines → not mergeable."""
    monkeypatch.setattr(
        ship_main, "_run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0)
    )
    monkeypatch.setattr(ship_main, "_git_output", lambda *_: "abc123")
    monkeypatch.setattr(
        ship_main.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 1, stdout="CONFLICT (content): Merge conflict in foo.py\n", stderr=""
        ),
    )
    ok, detail = ship_main._check_mergeability("/repo", "feat/x", "main", "origin")
    assert ok is False
    assert "CONFLICT" in detail


def test_check_mergeability_fetch_failure_skips(ship_main, monkeypatch):
    """If fetch fails, skip the check (returns True)."""

    def fail_run(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "git fetch")

    monkeypatch.setattr(ship_main, "_run", fail_run)
    ok, detail = ship_main._check_mergeability("/repo", "feat/x", "main", "origin")
    assert ok is True
    assert "skipping" in detail.lower()


def test_check_mergeability_no_false_positive_on_conflict_word(ship_main, monkeypatch):
    """Lines containing 'CONFLICT' but not starting with 'CONFLICT (' are ignored."""
    monkeypatch.setattr(
        ship_main, "_run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0)
    )
    monkeypatch.setattr(ship_main, "_git_output", lambda *_: "abc123")
    # Simulates merge-tree returning non-zero but output only contains
    # the word CONFLICT in source code or comments, not real conflicts.
    monkeypatch.setattr(
        ship_main.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 1, stdout="# This script detects CONFLICT in files\n", stderr=""
        ),
    )
    ok, detail = ship_main._check_mergeability("/repo", "feat/x", "main", "origin")
    assert ok is False
    # Should NOT report "Merge conflicts detected" — no real CONFLICT lines
    assert "Merge conflicts detected" not in detail
    assert "exit code" in detail


def test_check_mergeability_merge_base_failure_skips(ship_main, monkeypatch):
    """If merge-base fails (no common ancestor), skip the check."""
    call_count = {"n": 0}

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0)

    def fake_git_output(_repo, *args):
        call_count["n"] += 1
        if "merge-base" in args:
            raise subprocess.CalledProcessError(1, "git merge-base")
        return "abc123"

    monkeypatch.setattr(ship_main, "_run", fake_run)
    monkeypatch.setattr(ship_main, "_git_output", fake_git_output)
    ok, detail = ship_main._check_mergeability("/repo", "feat/x", "main", "origin")
    assert ok is True
    assert "skipping" in detail.lower()


# ── _post_merge_sync ─────────────────────────────────────────────────


def test_post_merge_sync_success(ship_main, monkeypatch, capsys):
    """Successful sync: checkout, pull, delete branch."""
    run_cmds = []

    def fake_run(cmd, **_kwargs):
        run_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ship_main, "_run", fake_run)
    monkeypatch.setattr(
        ship_main.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="", stderr=""),
    )

    ship_main._post_merge_sync("/repo", "main", "codex/ship-test")

    assert ["git", "checkout", "main"] in run_cmds
    assert ["git", "pull", "--ff-only"] in run_cmds
    out = capsys.readouterr().out
    assert "Deleted local branch" in out


def test_post_merge_sync_delete_failure(ship_main, monkeypatch, capsys):
    """Branch delete failure is non-fatal — prints warning."""
    monkeypatch.setattr(
        ship_main, "_run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0)
    )
    monkeypatch.setattr(
        ship_main.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 1, stdout="", stderr="error: branch not fully merged"
        ),
    )

    ship_main._post_merge_sync("/repo", "main", "codex/ship-test")
    out = capsys.readouterr().out
    assert "Could not delete" in out


# ── _auto_sync_branch ────────────────────────────────────────────────


def test_auto_sync_up_to_date(ship_main, monkeypatch, capsys):
    """No rebase when branch is not behind."""
    monkeypatch.setattr(ship_main, "_git_output", lambda *_: "0")
    ship_main._auto_sync_branch("/repo", "main", "origin")
    assert "up to date" in capsys.readouterr().out


def test_auto_sync_behind(ship_main, monkeypatch, capsys):
    """Rebase when behind remote base."""
    call_log = []

    def fake_git_output(_repo, *args):
        if "rev-list" in args:
            return "3"
        raise AssertionError(args)

    def fake_run(cmd, **_kwargs):
        call_log.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ship_main, "_git_output", fake_git_output)
    monkeypatch.setattr(ship_main, "_run", fake_run)
    ship_main._auto_sync_branch("/repo", "main", "origin")
    assert any("rebase" in cmd for cmd in call_log)
    assert "3 commit(s) behind" in capsys.readouterr().out


# ── _read_contract_config ────────────────────────────────────────────


def test_read_contract_config_missing(ship_main, tmp_path):
    """Returns empty dict when no contract exists."""
    assert ship_main._read_contract_config(str(tmp_path)) == {}


def test_read_contract_config_valid(ship_main, tmp_path):
    """Reads valid YAML contract."""
    (tmp_path / "gate_contract.yaml").write_text("version: '1.0'\nci:\n  x: 1\n")
    cfg = ship_main._read_contract_config(str(tmp_path))
    assert cfg["version"] == "1.0"
    assert cfg["ci"]["x"] == 1


def test_read_contract_config_invalid(ship_main, tmp_path):
    """Returns empty dict for invalid YAML."""
    (tmp_path / "gate_contract.yaml").write_text("{broken: yaml: [")
    assert ship_main._read_contract_config(str(tmp_path)) == {}


# ── main with --auto-sync ────────────────────────────────────────────


def test_main_auto_sync_flag(ship_main, monkeypatch):
    """--auto-sync triggers _auto_sync_branch before mergeability check."""
    repo_root = "/tmp/repo"
    seen = {"sync": False}

    def fake_git_output(_repo, *args):
        if args == ("rev-parse", "--show-toplevel"):
            return repo_root
        raise AssertionError(args)

    monkeypatch.setattr(ship_main, "_git_output", fake_git_output)
    monkeypatch.setattr(
        ship_main.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["gh"], returncode=0, stdout="", stderr=""
        ),
    )
    monkeypatch.setattr(ship_main, "_require_tool", lambda *_: None)
    monkeypatch.setattr(ship_main, "_require_clean_worktree", lambda *_: None)
    monkeypatch.setattr(ship_main, "_ensure_branch", lambda *_: "codex/ship-test")
    monkeypatch.setattr(
        ship_main, "_run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0)
    )
    monkeypatch.setattr(ship_main, "_auto_sync_branch", lambda *_: seen.__setitem__("sync", True))
    monkeypatch.setattr(ship_main, "_check_mergeability", lambda *_: (True, "clean"))
    monkeypatch.setattr(ship_main, "_run_local_gate_stack", lambda *_: None)
    monkeypatch.setattr(ship_main, "_push_branch", lambda *_: None)
    monkeypatch.setattr(ship_main, "_resolve_pr", lambda *_: (7, "https://example/pr/7"))

    old_argv = sys.argv
    sys.argv = ["ship_main.py", "--auto-sync"]
    try:
        assert ship_main.main() == 0
    finally:
        sys.argv = old_argv

    assert seen["sync"] is True


def test_main_mergeability_failure_raises(ship_main, monkeypatch):
    """Mergeability check failure raises RuntimeError before gates run."""
    repo_root = "/tmp/repo"
    seen = {"gate_ran": False}

    def fake_git_output(_repo, *args):
        if args == ("rev-parse", "--show-toplevel"):
            return repo_root
        raise AssertionError(args)

    monkeypatch.setattr(ship_main, "_git_output", fake_git_output)
    monkeypatch.setattr(
        ship_main.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["gh"], returncode=0, stdout="", stderr=""
        ),
    )
    monkeypatch.setattr(ship_main, "_require_tool", lambda *_: None)
    monkeypatch.setattr(ship_main, "_require_clean_worktree", lambda *_: None)
    monkeypatch.setattr(ship_main, "_ensure_branch", lambda *_: "codex/ship-test")
    monkeypatch.setattr(
        ship_main, "_run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0)
    )
    monkeypatch.setattr(ship_main, "_check_mergeability", lambda *_: (False, "CONFLICT in foo.py"))
    monkeypatch.setattr(
        ship_main,
        "_run_local_gate_stack",
        lambda *_: seen.__setitem__("gate_ran", True),
    )

    old_argv = sys.argv
    sys.argv = ["ship_main.py"]
    try:
        with pytest.raises(RuntimeError, match="cannot merge cleanly"):
            ship_main.main()
    finally:
        sys.argv = old_argv

    # Gates should NOT have run since mergeability check failed first
    assert seen["gate_ran"] is False


# ── Merge-tree edge cases (bug fix validation) ──────────────────────


def test_mergeability_conflict_in_source_not_flagged(ship_main, monkeypatch):
    """merge-tree output contains 'CONFLICT' as file content, not as a real conflict.

    The word CONFLICT can appear in file diffs (e.g., the ship script itself
    contains the word). Only lines starting with 'CONFLICT (' are real conflicts.
    """
    monkeypatch.setattr(
        ship_main, "_run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0)
    )
    monkeypatch.setattr(ship_main, "_git_output", lambda *_: "abc123")
    # merge-tree returns 0 (clean merge) but stdout has "CONFLICT" as content
    monkeypatch.setattr(
        ship_main.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [],
            0,
            stdout='        if "CONFLICT" in line or "conflict" in line:\n',
            stderr="",
        ),
    )
    ok, detail = ship_main._check_mergeability("/repo", "feat/x", "main", "origin")
    assert ok is True
    assert "cleanly" in detail


def test_mergeability_no_common_ancestor(ship_main, monkeypatch):
    """merge-tree returns nonzero with empty stdout → error, not conflict list."""
    monkeypatch.setattr(
        ship_main, "_run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0)
    )
    monkeypatch.setattr(ship_main, "_git_output", lambda *_: "abc123")
    monkeypatch.setattr(
        ship_main.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, stdout="", stderr=""),
    )
    ok, detail = ship_main._check_mergeability("/repo", "feat/x", "main", "origin")
    # Nonzero exit with no CONFLICT lines → not mergeable, but no specific conflicts listed.
    # The implementation correctly returns False with exit-code detail.
    assert ok is False
    assert "exit code" in detail


# ── _post_merge_sync ───────────────────────────────────────────────


def test_post_merge_sync(ship_main, monkeypatch):
    """Verify checkout + pull + branch delete are invoked in order."""
    calls = []

    def fake_run(cmd, *, cwd, check=True, capture=False):
        calls.append((cmd, cwd, check))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ship_main, "_run", fake_run)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0),
    )
    ship_main._post_merge_sync("/repo", "main", "codex/ship-test")

    assert len(calls) == 2
    assert calls[0][0] == ["git", "checkout", "main"]
    assert calls[0][1] == "/repo"
    assert calls[1][0] == ["git", "pull", "--ff-only"]
    assert calls[1][1] == "/repo"

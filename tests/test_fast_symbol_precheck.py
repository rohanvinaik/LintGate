from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "fast_symbol_precheck.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("fast_symbol_precheck", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_helper_import_loader_returns_callables() -> None:
    mod = _load_module()
    find_impacted_tests, collect_changed_python_files, run_symbol_gate = mod._load_lintgate_helpers()
    assert callable(find_impacted_tests)
    assert callable(collect_changed_python_files)
    assert callable(run_symbol_gate)


def test_helper_import_loader_inserts_repo_root_when_missing(monkeypatch) -> None:
    mod = _load_module()
    repo_root = str(Path(mod.__file__).resolve().parents[1])
    filtered = [p for p in mod.sys.path if p != repo_root]
    monkeypatch.setattr(mod.sys, "path", filtered)

    mod._load_lintgate_helpers()
    assert mod.sys.path[0] == repo_root


def test_source_and_test_path_classification() -> None:
    mod = _load_module()
    assert mod._is_source_file("/tmp/repo/lintgate/foo.py") is True
    assert mod._is_source_file("/tmp/repo/tests/test_foo.py") is False
    assert mod._is_source_file("/tmp/repo/test/test_foo.py") is False
    assert mod._is_source_file("/tmp/repo/lintgate/foo.txt") is False
    assert mod._is_test_file("/tmp/repo/tests/test_foo.py") is True
    assert mod._is_test_file("/tmp/repo/lintgate/conftest.py") is True
    assert mod._is_test_file("/tmp/repo/lintgate/foo.py") is False
    assert mod._is_test_file("/tmp/repo/lintgate/foo.txt") is False


def test_head_has_parent_true_false(monkeypatch) -> None:
    mod = _load_module()

    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(["git"], returncode=0),
    )
    assert mod._head_has_parent("/tmp/repo") is True

    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(["git"], returncode=1),
    )
    assert mod._head_has_parent("/tmp/repo") is False


def test_no_changed_source_files_returns_zero(monkeypatch, capsys) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "_head_has_parent", lambda _root: True)
    monkeypatch.setattr(
        mod,
        "_load_lintgate_helpers",
        lambda: (
            lambda *_args, **_kwargs: [],
            lambda *_args, **_kwargs: [],
            lambda **_kwargs: 0,
        ),
    )

    code = mod.run_precheck("/tmp/repo")
    out = capsys.readouterr().out
    assert code == 0
    assert "no changed source Python files" in out


def test_changed_source_without_impacted_tests_fails(monkeypatch, capsys) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "_head_has_parent", lambda _root: True)
    monkeypatch.setattr(
        mod,
        "_load_lintgate_helpers",
        lambda: (
            lambda *_args, **_kwargs: [],
            lambda *_args, **_kwargs: ["/tmp/repo/scripts/new_tool.py"],
            lambda **_kwargs: 0,
        ),
    )

    code = mod.run_precheck("/tmp/repo")
    out = capsys.readouterr().out
    assert code == 1
    assert "no impacted tests found" in out


def test_pytest_failure_returns_nonzero(monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "_head_has_parent", lambda _root: True)
    monkeypatch.setattr(
        mod,
        "_load_lintgate_helpers",
        lambda: (
            lambda *_args, **_kwargs: ["/tmp/repo/tests/test_new_tool.py"],
            lambda *_args, **_kwargs: ["/tmp/repo/scripts/new_tool.py"],
            lambda **_kwargs: 0,
        ),
    )
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(["pytest"], returncode=3),
    )

    code = mod.run_precheck("/tmp/repo")
    assert code == 3


def test_success_path_runs_symbol_gate(monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "_head_has_parent", lambda _root: True)
    seen: dict[str, object] = {}

    def fake_run_symbol_gate(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(
        mod,
        "_load_lintgate_helpers",
        lambda: (
            lambda *_args, **_kwargs: ["/tmp/repo/tests/test_new_tool.py"],
            lambda *_args, **_kwargs: ["/tmp/repo/scripts/new_tool.py"],
            fake_run_symbol_gate,
        ),
    )
    seen_cmd: dict[str, object] = {}

    def fake_subprocess_run(cmd, **_kwargs):
        seen_cmd["cmd"] = cmd
        return subprocess.CompletedProcess(["pytest"], returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_subprocess_run)

    code = mod.run_precheck("/tmp/repo")
    assert code == 0
    assert seen["surface"] == "prepush_fast"
    assert seen["explicit_files"] is None
    assert seen["base"] == "HEAD~1"
    assert seen["head"] == "HEAD"
    assert "--cov-fail-under=0" in seen_cmd["cmd"]


def test_changed_tests_are_included_in_fast_precheck(monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "_head_has_parent", lambda _root: True)

    seen_cmd: dict[str, object] = {}

    def fake_subprocess_run(cmd, **_kwargs):
        seen_cmd["cmd"] = cmd
        return subprocess.CompletedProcess(["pytest"], returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(
        mod,
        "_load_lintgate_helpers",
        lambda: (
            lambda *_args, **_kwargs: [],
            lambda *_args, **_kwargs: [
                "/tmp/repo/scripts/new_tool.py",
                "/tmp/repo/tests/test_new_tool.py",
            ],
            lambda **_kwargs: 0,
        ),
    )

    code = mod.run_precheck("/tmp/repo")
    assert code == 0
    assert "/tmp/repo/tests/test_new_tool.py" in seen_cmd["cmd"]


def test_main_calls_run_precheck(monkeypatch) -> None:
    mod = _load_module()
    monkeypatch.setattr(mod, "run_precheck", lambda _root: 0)
    code = mod.main(["--project-root", "/tmp/repo"])
    assert code == 0

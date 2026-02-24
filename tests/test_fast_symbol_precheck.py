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
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(["pytest"], returncode=0),
    )

    code = mod.run_precheck("/tmp/repo")
    assert code == 0
    assert seen["surface"] == "prepush_fast"
    assert seen["explicit_files"] == ["/tmp/repo/scripts/new_tool.py"]

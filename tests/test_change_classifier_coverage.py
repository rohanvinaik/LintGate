"""Targeted coverage tests for change_classifier.py uncovered symbols."""

from __future__ import annotations

import os

from lintgate.change_classifier import (
    _classify_no_file_change,
    _is_build_command,
    _is_config_file,
    _is_dependency_file,
    _is_docs_file,
    _is_readonly_bash,
    _is_test_file,
    _matches_pipeline_path,
    classify_change,
)


# ── _is_dependency_file ──────────────────────────────────────────────


def test_dependency_file_requirements():
    assert _is_dependency_file("requirements.txt") is True


def test_dependency_file_pipfile_lock():
    assert _is_dependency_file("Pipfile.lock") is True


def test_dependency_file_normal():
    assert _is_dependency_file("app.py") is False


# ── _is_test_file ────────────────────────────────────────────────────


def test_test_file_test_prefix():
    assert _is_test_file("tests/test_foo.py") is True


def test_test_file_conftest():
    assert _is_test_file("tests/conftest.py") is True


def test_test_file_source():
    assert _is_test_file("src/app.py") is False


# ── _is_readonly_bash ────────────────────────────────────────────────


def test_readonly_git_log():
    assert _is_readonly_bash("git log --oneline") is True


def test_readonly_cat():
    assert _is_readonly_bash("cat foo.py") is True


def test_readonly_not_string():
    assert _is_readonly_bash(123) is False


def test_readonly_empty():
    assert _is_readonly_bash("") is True


def test_readonly_write_command():
    assert _is_readonly_bash("python setup.py install") is False


# ── _is_build_command ────────────────────────────────────────────────


def test_build_pip_install():
    assert _is_build_command("pip install requests") is True


def test_build_not_string():
    assert _is_build_command(None) is False


def test_build_normal():
    assert _is_build_command("ls -la") is False


# ── _matches_pipeline_path ───────────────────────────────────────────


def test_pipeline_match_directory(tmp_path):
    f = tmp_path / "lintgate" / "hook.py"
    f.parent.mkdir()
    f.touch()
    assert _matches_pipeline_path(str(f), ["lintgate/"], str(tmp_path)) is True


def test_pipeline_match_exact_file(tmp_path):
    f = tmp_path / "hook.py"
    f.touch()
    assert _matches_pipeline_path(str(f), ["hook.py"], str(tmp_path)) is True


def test_pipeline_no_match(tmp_path):
    f = tmp_path / "other.py"
    f.touch()
    assert _matches_pipeline_path(str(f), ["lintgate/"], str(tmp_path)) is False


def test_pipeline_empty_filepath():
    assert _matches_pipeline_path("", ["lintgate/"], "/tmp") is False


def test_pipeline_empty_cwd():
    assert _matches_pipeline_path("/foo.py", ["lintgate/"], "") is False


def test_pipeline_not_string():
    assert _matches_pipeline_path(123, ["lintgate/"], "/tmp") is False


# ── _classify_no_file_change ─────────────────────────────────────────


def test_no_file_bash_build():
    r = _classify_no_file_change("Bash", {"command": "pip install foo"})
    assert r.change_kind == "build"


def test_no_file_bash_non_build():
    r = _classify_no_file_change("Bash", {"command": "ls"})
    assert r.risk_level == "none"


def test_no_file_other_tool():
    r = _classify_no_file_change("Read", {})
    assert r.risk_level == "none"


# ── classify_change ──────────────────────────────────────────────────


def test_classify_edit(tmp_path):
    from lintgate.types import ProjectConfig
    f = tmp_path / "app.py"
    f.write_text("print('hello')\n")
    r = classify_change(
        tool_name="Edit",
        tool_input={"file_path": str(f), "old_string": "hello", "new_string": "world"},
        tool_output="ok",
        cwd=str(tmp_path),
    )
    assert r.files_changed == [str(f)]


def test_classify_write_new_file(tmp_path):
    f = tmp_path / "new.py"
    r = classify_change(
        tool_name="Write",
        tool_input={"file_path": str(f), "content": "x = 1"},
        tool_output="ok",
        cwd=str(tmp_path),
    )
    assert r.is_new_file is True


def test_classify_bash_readonly(tmp_path):
    r = classify_change(
        tool_name="Bash",
        tool_input={"command": "git status"},
        tool_output="",
        cwd=str(tmp_path),
    )
    assert r.risk_level == "none"


def test_classify_docs_file(tmp_path):
    f = tmp_path / "README.md"
    f.write_text("# Hello\n")
    r = classify_change(
        tool_name="Write",
        tool_input={"file_path": str(f), "content": "# Updated"},
        tool_output="ok",
        cwd=str(tmp_path),
    )
    assert r.change_kind == "docs"


def test_classify_config_file(tmp_path):
    f = tmp_path / "setup.cfg"
    f.write_text("[metadata]\n")
    r = classify_change(
        tool_name="Write",
        tool_input={"file_path": str(f), "content": "[metadata]\nfoo=1"},
        tool_output="ok",
        cwd=str(tmp_path),
    )
    assert r.change_kind == "config"


def test_classify_test_file(tmp_path):
    f = tmp_path / "tests" / "test_app.py"
    f.parent.mkdir()
    f.write_text("def test_it(): pass\n")
    r = classify_change(
        tool_name="Write",
        tool_input={"file_path": str(f), "content": "def test_it(): assert True"},
        tool_output="ok",
        cwd=str(tmp_path),
    )
    assert r.touches_test_files is True


def test_classify_pipeline_critical(tmp_path):
    from lintgate.types import ProjectConfig
    f = tmp_path / "lintgate" / "hook.py"
    f.parent.mkdir()
    f.write_text("x=1\n")
    cfg = ProjectConfig(pipeline_critical_paths=["lintgate/"])
    r = classify_change(
        tool_name="Write",
        tool_input={"file_path": str(f), "content": "x=2"},
        tool_output="ok",
        cwd=str(tmp_path),
        config=cfg,
    )
    assert r.touches_pipeline_critical is True

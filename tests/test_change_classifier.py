import os

from lintgate.change_classifier import (
    _classify_no_file_change,
    _is_build_command,
    _is_dependency_file,
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


# ── _extract_changed_files ───────────────────────────────────────────


def test_extract_files_bash_unknown_command(tmp_path):
    # Non-build, non-readonly bash command should return []
    r = classify_change(
        tool_name="Bash",
        tool_input={"command": "touch foo.txt"},
        tool_output="",
        cwd=str(tmp_path),
    )
    assert r.files_changed == []


# ── _resolve_path ────────────────────────────────────────────────────


def test_resolve_path_invalid_input():
    from lintgate.change_classifier import _resolve_path

    assert _resolve_path(None, "/tmp") == ""
    assert _resolve_path("", "/tmp") == ""
    assert _resolve_path("foo.py", None) == os.path.normpath(os.path.join(os.getcwd(), "foo.py"))


# ── _matches_pipeline_path ───────────────────────────────────────────


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


def test_classify_multi_edit(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("x = 1\ny = 2\n")
    r = classify_change(
        tool_name="MultiEdit",
        tool_input={
            "file_path": str(f),
            "edits": [
                {"old_string": "x = 1", "new_string": "x = 10"},
                {"old_string": "y = 2", "new_string": "y = 20"},
            ],
        },
        tool_output="ok",
        cwd=str(tmp_path),
    )
    assert r.lines_added == 2
    assert r.lines_removed == 2


def test_classify_import_change(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("import os\n")
    r = classify_change(
        tool_name="Edit",
        tool_input={
            "file_path": str(f),
            "old_string": "import os",
            "new_string": "import sys",
        },
        tool_output="ok",
        cwd=str(tmp_path),
    )
    assert r.change_kind == "import"


def test_classify_structural_change(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("def old(): pass\n")
    r = classify_change(
        tool_name="Edit",
        tool_input={
            "file_path": str(f),
            "old_string": "def old():",
            "new_string": "def new():",
        },
        tool_output="ok",
        cwd=str(tmp_path),
    )
    assert r.change_kind == "structural"


def test_classify_dependency_change(tmp_path):
    # Use poetry.lock which is not a doc extension
    f = tmp_path / "poetry.lock"
    r = classify_change(
        tool_name="Write",
        tool_input={"file_path": str(f), "content": "[metadata]\nversion = '1.0'"},
        tool_output="ok",
        cwd=str(tmp_path),
    )
    assert r.change_kind == "dependency"


def test_classify_architectural_multi_file(tmp_path):
    # Multiple structural changes across files = architectural
    _f1 = tmp_path / "f1.py"
    _f2 = tmp_path / "f2.py"
    _f3 = tmp_path / "f3.py"
    # Note: classify_change currently only supports one file per tool use for Write/Edit/MultiEdit.
    # However, the underlying _classify_risk takes a list of files.
    # We can test this by mocking if needed, or if MultiEdit supports multiple files (it doesn't in the code).
    # wait, MultiEdit in the code only extracts one file_path.
    # But _classify_risk logic: if len(files) >= 3 and kind == "structural": return "architectural"
    from lintgate.change_classifier import _classify_risk
    from lintgate.types import DiffAnalysis

    diff = DiffAnalysis(class_structure_changed=True)
    risk = _classify_risk("structural", diff, ["a.py", "b.py", "c.py"], False)
    assert risk == "architectural"


def test_classify_risk_none():
    from lintgate.change_classifier import _classify_risk
    from lintgate.types import DiffAnalysis

    assert _classify_risk("logic", DiffAnalysis.empty(), [], False) == "none"

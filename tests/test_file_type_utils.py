import os

from lintgate.change_classifiers import file_type_utils

# ── _is_dependency_file ──────────────────────────────────────────────


def test_dependency_file_requirements():
    assert file_type_utils._is_dependency_file("requirements.txt") is True


def test_dependency_file_pipfile_lock():
    assert file_type_utils._is_dependency_file("Pipfile.lock") is True


def test_dependency_file_normal():
    assert file_type_utils._is_dependency_file("app.py") is False


# ── _is_test_file ────────────────────────────────────────────────────


def test_test_file_test_prefix():
    assert file_type_utils._is_test_file("tests/test_foo.py") is True


def test_test_file_conftest():
    assert file_type_utils._is_test_file("tests/conftest.py") is True


def test_test_file_source():
    assert file_type_utils._is_test_file("src/app.py") is False


# ── _is_readonly_bash ────────────────────────────────────────────────


def test_readonly_git_log():
    assert file_type_utils._is_readonly_bash("git log --oneline") is True


def test_readonly_cat():
    assert file_type_utils._is_readonly_bash("cat foo.py") is True


def test_readonly_not_string():
    assert file_type_utils._is_readonly_bash(str(123)) is False


def test_readonly_empty():
    assert file_type_utils._is_readonly_bash("") is True


def test_readonly_write_command():
    assert file_type_utils._is_readonly_bash("python setup.py install") is False


# ── _is_build_command ────────────────────────────────────────────────


def test_build_pip_install():
    assert file_type_utils._is_build_command("pip install requests") is True


def test_build_not_string():
    assert file_type_utils._is_build_command(str(None)) is False


def test_build_normal():
    assert file_type_utils._is_build_command("ls -la") is False


# ── _matches_pipeline_path ───────────────────────────────────────────


def test_pipeline_match_directory(tmp_path):
    f = tmp_path / "lintgate" / "hook.py"
    f.parent.mkdir()
    f.touch()
    assert file_type_utils._matches_pipeline_path(str(f), ["lintgate/"], str(tmp_path)) is True


def test_pipeline_match_exact_file(tmp_path):
    f = tmp_path / "hook.py"
    f.touch()
    assert file_type_utils._matches_pipeline_path(str(f), ["hook.py"], str(tmp_path)) is True


def test_pipeline_no_match(tmp_path):
    f = tmp_path / "other.py"
    f.touch()
    assert file_type_utils._matches_pipeline_path(str(f), ["lintgate/"], str(tmp_path)) is False


def test_pipeline_empty_filepath():
    assert file_type_utils._matches_pipeline_path("", ["lintgate/"], "/tmp") is False


def test_pipeline_empty_cwd():
    assert file_type_utils._matches_pipeline_path("/foo.py", ["lintgate/"], "") is False


# ── _resolve_path ────────────────────────────────────────────────────


def test_resolve_path_invalid_input():
    from lintgate.change_classifiers.file_type_utils import _resolve_path

    assert _resolve_path(str(None), "/tmp") == ""
    assert _resolve_path("", "/tmp") == ""
    assert _resolve_path("foo.py", str(None)) == os.path.normpath(
        os.path.join(os.getcwd(), "foo.py")
    )


def test_resolve_path_absolute_filepath():
    """Covers _resolve_path line 133: absolute filepath returned as-is."""
    from lintgate.change_classifiers.file_type_utils import _resolve_path

    assert _resolve_path("/usr/local/bin/python", "/tmp") == "/usr/local/bin/python"


# ── _is_docs_file ──────────────────────────────────────────────────


def test_is_docs_file_markdown():
    """Covers _is_docs_file lines 138-139: .md is a docs extension."""
    assert file_type_utils._is_docs_file("README.md") is True


def test_is_docs_file_rst():
    assert file_type_utils._is_docs_file("docs/guide.rst") is True


def test_is_docs_file_case_insensitive():
    assert file_type_utils._is_docs_file("CHANGES.TXT") is True


def test_is_docs_file_python():
    assert file_type_utils._is_docs_file("app.py") is False


# ── _is_config_file ────────────────────────────────────────────────


def test_is_config_file_yaml():
    """Covers _is_config_file lines 143-144: .yaml is a config extension."""
    assert file_type_utils._is_config_file("config.yaml") is True


def test_is_config_file_toml():
    assert file_type_utils._is_config_file("settings.toml") is True


def test_is_config_file_by_name():
    """Covers the p.name in _CONFIG_NAMES branch."""
    assert file_type_utils._is_config_file("Makefile") is True
    assert file_type_utils._is_config_file("Dockerfile") is True
    assert file_type_utils._is_config_file(".gitignore") is True


def test_is_config_file_python():
    assert file_type_utils._is_config_file("app.py") is False


# ── _is_readonly_bash (non-string input) ───────────────────────────


def test_readonly_bash_non_string_input():
    """Covers _is_readonly_bash line 159: non-string returns False."""
    assert file_type_utils._is_readonly_bash(123) is False  # type: ignore[arg-type]
    assert file_type_utils._is_readonly_bash(None) is False  # type: ignore[arg-type]


# ── _is_build_command (non-string input) ───────────────────────────


def test_build_command_non_string_input():
    """Covers _is_build_command line 169: non-string returns False."""
    assert file_type_utils._is_build_command(42) is False  # type: ignore[arg-type]
    assert file_type_utils._is_build_command(None) is False  # type: ignore[arg-type]


# ── _matches_pipeline_path (ValueError branch) ────────────────────


def test_pipeline_path_relpath_value_error():
    """Covers _matches_pipeline_path lines 183-184: ValueError from os.path.relpath.

    On Windows, relpath raises ValueError when filepath and cwd are on
    different drives. On Unix we can trigger it by passing paths that
    cause relpath to raise (or we mock it).
    """
    import unittest.mock

    with unittest.mock.patch("os.path.relpath", side_effect=ValueError("cross-drive")):
        result = file_type_utils._matches_pipeline_path("/d/some/file.py", ["some/"], "/c/project")
        assert result is False

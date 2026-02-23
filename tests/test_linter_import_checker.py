"""Tests for import_checker linter."""

from __future__ import annotations

import ast
from unittest.mock import MagicMock, patch

from lintgate.linters.import_checker import (
    ImportChecker,
    _collect_guarded_import_lines,
)
from lintgate.types import LinterContext

# ── _collect_guarded_import_lines ────────────────────────────────────


def test_guarded_lines_try_import_error():
    src = "try:\n    import tomllib\nexcept ImportError:\n    import tomli as tomllib\n"
    tree = ast.parse(src)
    guarded = _collect_guarded_import_lines(tree)
    assert len(guarded) == 2


def test_guarded_lines_try_module_not_found():
    src = "try:\n    import tomllib\nexcept ModuleNotFoundError:\n    pass\n"
    tree = ast.parse(src)
    guarded = _collect_guarded_import_lines(tree)
    assert len(guarded) >= 1


def test_guarded_lines_bare_except():
    src = "try:\n    import foo\nexcept:\n    pass\n"
    tree = ast.parse(src)
    guarded = _collect_guarded_import_lines(tree)
    assert len(guarded) >= 1


def test_guarded_lines_tuple_handler():
    src = "try:\n    import foo\nexcept (ImportError, ModuleNotFoundError):\n    pass\n"
    tree = ast.parse(src)
    guarded = _collect_guarded_import_lines(tree)
    assert len(guarded) >= 1


def test_guarded_lines_no_try():
    src = "import os\n"
    tree = ast.parse(src)
    guarded = _collect_guarded_import_lines(tree)
    assert guarded == set()


def test_guarded_lines_try_non_import_handler():
    src = "try:\n    import foo\nexcept ValueError:\n    pass\n"
    tree = ast.parse(src)
    guarded = _collect_guarded_import_lines(tree)
    assert guarded == set()


# ── ImportChecker ────────────────────────────────────────────────────


def _make_ctx(tmp_path, files=None, config=None):
    return LinterContext(
        files=files or [],
        project_root=str(tmp_path),
        strictness="normal",
        config=config or {},
    )


def test_check_file_valid_import(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("import os\n")
    ctx = _make_ctx(tmp_path, files=[str(f)])
    checker = ImportChecker()
    issues = list(checker.run(ctx))
    assert issues == []


def test_check_file_bad_import(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("import nonexistent_fake_module_xyz\n")
    ctx = _make_ctx(tmp_path, files=[str(f)])
    checker = ImportChecker()
    issues = list(checker.run(ctx))
    assert len(issues) == 1
    assert issues[0].kind == "unresolved-import"


def test_check_file_bad_from_import(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("from nonexistent_fake_xyz import thing\n")
    ctx = _make_ctx(tmp_path, files=[str(f)])
    checker = ImportChecker()
    issues = list(checker.run(ctx))
    assert len(issues) == 1
    assert issues[0].kind == "unresolved-import"


def test_check_file_relative_import_skipped(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("from . import sibling\n")
    ctx = _make_ctx(tmp_path, files=[str(f)])
    checker = ImportChecker()
    issues = list(checker.run(ctx))
    assert issues == []


def test_check_file_guarded_import_skipped(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("try:\n    import tomllib\nexcept ImportError:\n    import tomli as tomllib\n")
    ctx = _make_ctx(tmp_path, files=[str(f)])
    checker = ImportChecker()
    issues = list(checker.run(ctx))
    assert issues == []


def test_check_file_syntax_error(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def broken(\n")
    ctx = _make_ctx(tmp_path, files=[str(f)])
    checker = ImportChecker()
    issues = list(checker.run(ctx))
    assert issues == []


def test_check_file_missing_file(tmp_path):
    ctx = _make_ctx(tmp_path, files=[str(tmp_path / "missing.py")])
    checker = ImportChecker()
    issues = list(checker.run(ctx))
    assert issues == []


def test_check_local_module_exists(tmp_path):
    (tmp_path / "mymod.py").write_text("x = 1\n")
    f = tmp_path / "main.py"
    f.write_text("import mymod\n")
    ctx = _make_ctx(tmp_path, files=[str(f)])
    checker = ImportChecker()
    issues = list(checker.run(ctx))
    assert issues == []


def test_check_local_package_exists(tmp_path):
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    f = tmp_path / "main.py"
    f.write_text("import mypkg\n")
    ctx = _make_ctx(tmp_path, files=[str(f)])
    checker = ImportChecker()
    issues = list(checker.run(ctx))
    assert issues == []


def test_custom_command(tmp_path):
    ctx = _make_ctx(tmp_path, config={"command": "echo ok"})
    checker = ImportChecker()
    mock_result = MagicMock(returncode=0, stdout="", stderr="")
    with patch.object(checker, "run_command", return_value=mock_result):
        issues = list(checker.run(ctx))
    assert issues == []


def test_custom_command_failure(tmp_path):
    ctx = _make_ctx(tmp_path, config={"command": "false"})
    checker = ImportChecker()
    mock_result = MagicMock(returncode=1, stdout="", stderr="import failed")
    with patch.object(checker, "run_command", return_value=mock_result):
        issues = list(checker.run(ctx))
    assert len(issues) == 1
    assert issues[0].kind == "import-verify-failed"

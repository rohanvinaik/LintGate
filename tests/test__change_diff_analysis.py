"""Tests for lintgate/change_classifiers/_change_diff_analysis.py."""

from __future__ import annotations

from lintgate.change_classifiers._change_diff_analysis import (
    _analyze_diff,
    _as_text,
    _extract_changed_files,
    _group_by_language,
)
from lintgate.types import DiffAnalysis

# ── _as_text ────────────────────────────────────────────────────────────


def test_as_text_string():
    assert _as_text("hello") == "hello"


def test_as_text_non_string():
    assert _as_text(123) == ""
    assert _as_text(None) == ""
    assert _as_text(["a"]) == ""


# ── _extract_changed_files ──────────────────────────────────────────────


def test_extract_changed_files_edit():
    files = _extract_changed_files("Edit", {"file_path": "/tmp/foo.py"}, "/tmp")
    assert files == ["/tmp/foo.py"]


def test_extract_changed_files_write():
    files = _extract_changed_files("Write", {"file_path": "bar.py"}, "/project")
    assert len(files) == 1
    assert files[0].endswith("bar.py")


def test_extract_changed_files_bash_readonly():
    files = _extract_changed_files("Bash", {"command": "cat foo.py"}, "/tmp")
    assert files == []


def test_extract_changed_files_bash_build():
    files = _extract_changed_files("Bash", {"command": "pip install requests"}, "/tmp")
    assert files == []


def test_extract_changed_files_bash_other():
    files = _extract_changed_files("Bash", {"command": "python run.py"}, "/tmp")
    assert files == []


def test_extract_changed_files_unknown_tool():
    files = _extract_changed_files("Read", {"file_path": "/tmp/x.py"}, "/tmp")
    assert files == []


def test_extract_changed_files_empty_path():
    files = _extract_changed_files("Edit", {"file_path": ""}, "/tmp")
    assert files == []


# ── _group_by_language ──────────────────────────────────────────────────


def test_group_by_language_mixed():
    files = ["/a/b.py", "/c/d.ts", "/e/f.rs", "/g/h.txt"]
    groups = _group_by_language(files)
    assert groups["python"] == ["/a/b.py"]
    assert groups["typescript"] == ["/c/d.ts"]
    assert groups["rust"] == ["/e/f.rs"]
    assert groups["other"] == ["/g/h.txt"]


def test_group_by_language_empty():
    assert _group_by_language([]) == {}


def test_group_by_language_all_python():
    files = ["/a.py", "/b.py"]
    groups = _group_by_language(files)
    assert groups == {"python": ["/a.py", "/b.py"]}


# ── _analyze_diff ───────────────────────────────────────────────────────


def test_analyze_diff_edit_import_only():
    result = _analyze_diff(
        "Edit",
        {
            "old_string": "import os\n",
            "new_string": "import os\nimport sys\n",
        },
    )
    assert result.import_only is True
    assert result.lines_added == 1
    assert result.lines_removed == 0
    assert result.is_new_file is False


def test_analyze_diff_write_new_file():
    result = _analyze_diff(
        "Write",
        {"content": "def foo():\n    return 1\n"},
    )
    assert result.is_new_file is True
    assert result.lines_added == 2


def test_analyze_diff_edit_function_sig():
    result = _analyze_diff(
        "Edit",
        {
            "old_string": "def foo(x):\n    return x\n",
            "new_string": "def foo(x, y):\n    return x + y\n",
        },
    )
    assert result.function_signatures_changed is True


def test_analyze_diff_edit_class_structure():
    result = _analyze_diff(
        "Edit",
        {
            "old_string": "x = 1\n",
            "new_string": "class Bar:\n    x = 1\n",
        },
    )
    assert result.class_structure_changed is True


def test_analyze_diff_formatting_only():
    result = _analyze_diff(
        "Edit",
        {
            "old_string": "x=1\ny =  2\n",
            "new_string": "x = 1\ny = 2\n",
        },
    )
    assert result.formatting_only is True


def test_analyze_diff_bash_build():
    result = _analyze_diff("Bash", {"command": "pip install foo"})
    assert result.is_build_command is True


def test_analyze_diff_unknown_tool():
    result = _analyze_diff("Read", {})
    assert result == DiffAnalysis.empty()


def test_analyze_diff_multiedit():
    result = _analyze_diff(
        "MultiEdit",
        {
            "edits": [
                {"old_string": "a = 1\n", "new_string": "a = 2\n"},
                {"old_string": "b = 3\n", "new_string": "b = 4\n"},
            ]
        },
    )
    assert result.lines_added >= 1
    assert result.is_new_file is False


def test_analyze_diff_multiedit_bad_edits():
    result = _analyze_diff("MultiEdit", {"edits": "not a list"})
    assert result.lines_added == 0

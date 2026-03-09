"""Tests for bandit_fast_linter.py — _is_test_or_docs_context and _is_noqa_suppressed.

The existing test_bandit_fast.py covers BanditFastLinter.run and metadata.
This file targets the two helper functions that had 100% mutation survival.
"""

from __future__ import annotations

from lintgate.linters.bandit_fast_linter import (
    _is_noqa_suppressed,
    _is_test_or_docs_context,
)

# ── _is_test_or_docs_context ────────────────────────────────────────


class TestIsTestOrDocsContext:
    def test_file_in_tests_dir(self):
        assert _is_test_or_docs_context("/project/tests/test_foo.py", "/project") is True

    def test_file_in_test_dir(self):
        assert _is_test_or_docs_context("/project/test/helper.py", "/project") is True

    def test_file_in_testing_dir(self):
        assert _is_test_or_docs_context("/project/testing/util.py", "/project") is True

    def test_file_in_docs_dir(self):
        assert _is_test_or_docs_context("/project/docs/example.py", "/project") is True

    def test_file_in_doc_dir(self):
        assert _is_test_or_docs_context("/project/doc/sample.py", "/project") is True

    def test_file_in_examples_dir(self):
        assert _is_test_or_docs_context("/project/examples/demo.py", "/project") is True

    def test_file_in_fixtures_dir(self):
        assert _is_test_or_docs_context("/project/fixtures/data.py", "/project") is True

    def test_file_in_conftest_dir(self):
        assert _is_test_or_docs_context("/project/conftest/helper.py", "/project") is True

    def test_file_in_src_dir_not_test(self):
        assert _is_test_or_docs_context("/project/src/core.py", "/project") is False

    def test_file_at_project_root(self):
        assert _is_test_or_docs_context("/project/setup.py", "/project") is False

    def test_file_in_nested_test_dir(self):
        assert _is_test_or_docs_context("/project/src/tests/test_core.py", "/project") is True

    def test_file_named_tests_but_not_dir(self):
        # File named "tests.py" at root — "tests" is the filename, not a directory part
        assert _is_test_or_docs_context("/project/tests.py", "/project") is False

    def test_case_insensitive_match(self):
        assert _is_test_or_docs_context("/project/Tests/test_foo.py", "/project") is True
        assert _is_test_or_docs_context("/project/DOCS/example.py", "/project") is True

    def test_deeply_nested(self):
        assert _is_test_or_docs_context(
            "/project/src/lib/tests/unit/test_core.py", "/project"
        ) is True

    def test_non_matching_dir(self):
        assert _is_test_or_docs_context("/project/src/lib/core.py", "/project") is False

    def test_relpath_value_error(self):
        # On Windows this could cause ValueError for different drives; on Unix
        # we can't easily trigger ValueError, but the function catches it
        # gracefully. Test the normal non-test path as a proxy.
        assert _is_test_or_docs_context("/other/path/file.py", "/project") is False


# ── _is_noqa_suppressed ─────────────────────────────────────────────


class TestIsNoqaSuppressed:
    def test_suppressed_by_exact_code(self, tmp_path):
        src = tmp_path / "code.py"
        src.write_text("password = 'secret'  # noqa: B105\n")
        assert _is_noqa_suppressed(str(src), 1, "B105") is True

    def test_not_suppressed_different_code(self, tmp_path):
        src = tmp_path / "code.py"
        src.write_text("password = 'secret'  # noqa: B106\n")
        assert _is_noqa_suppressed(str(src), 1, "B105") is False

    def test_suppressed_by_s_alias(self, tmp_path):
        src = tmp_path / "code.py"
        src.write_text("query = f'SELECT {x}'  # noqa: S608\n")
        assert _is_noqa_suppressed(str(src), 1, "B608") is True

    def test_suppressed_in_comma_list(self, tmp_path):
        src = tmp_path / "code.py"
        src.write_text("x = eval(y)  # noqa: B301, B302\n")
        assert _is_noqa_suppressed(str(src), 1, "B301") is True
        assert _is_noqa_suppressed(str(src), 1, "B302") is True

    def test_not_suppressed_no_noqa(self, tmp_path):
        src = tmp_path / "code.py"
        src.write_text("password = 'secret'\n")
        assert _is_noqa_suppressed(str(src), 1, "B105") is False

    def test_wrong_line_number(self, tmp_path):
        src = tmp_path / "code.py"
        src.write_text("line1\npassword = 'secret'  # noqa: B105\n")
        assert _is_noqa_suppressed(str(src), 1, "B105") is False
        assert _is_noqa_suppressed(str(src), 2, "B105") is True

    def test_nonexistent_file(self):
        assert _is_noqa_suppressed("/no/such/file.py", 1, "B105") is False

    def test_empty_filepath(self):
        assert _is_noqa_suppressed("", 1, "B105") is False

    def test_zero_lineno(self):
        assert _is_noqa_suppressed("/some/file.py", 0, "B105") is False

    def test_empty_test_id(self, tmp_path):
        src = tmp_path / "code.py"
        src.write_text("x = 1  # noqa: B105\n")
        assert _is_noqa_suppressed(str(src), 1, "") is False

    def test_line_past_end_of_file(self, tmp_path):
        src = tmp_path / "code.py"
        src.write_text("only one line  # noqa: B105\n")
        assert _is_noqa_suppressed(str(src), 99, "B105") is False

    def test_multiple_lines_correct_match(self, tmp_path):
        src = tmp_path / "code.py"
        src.write_text("line1\nline2  # noqa: B602\nline3  # noqa: B105\n")
        assert _is_noqa_suppressed(str(src), 2, "B602") is True
        assert _is_noqa_suppressed(str(src), 3, "B105") is True
        assert _is_noqa_suppressed(str(src), 2, "B105") is False

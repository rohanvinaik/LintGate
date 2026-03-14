"""Tests for lintgate/channels/_test_hygiene_duplicates.py.

Covers duplicate detection, cross-file fingerprinting,
subsumption analysis, and the THYGIENE003/THYGIENE005 pipeline.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

from lintgate.channels._test_hygiene_duplicates import (
    _add_subsumption_findings,
    _build_test_fingerprints,
    _find_cross_file_duplicates,
    _thygiene003_duplicates,
)

if TYPE_CHECKING:
    from lintgate.controlplane.types import RepairAction
    from lintgate.types import LintIssue


def _write_test_file(path, content: str) -> str:
    path.write_text(textwrap.dedent(content))
    return str(path)


# ── _build_test_fingerprints ─────────────────────────────────────


class TestBuildTestFingerprints:
    def test_empty_list(self):
        assert _build_test_fingerprints([]) == []

    def test_single_test_function(self, tmp_path):
        f = _write_test_file(
            tmp_path / "test_a.py",
            """\
            def test_foo():
                assert 1 == 1
            """,
        )
        fps = _build_test_fingerprints([f])
        assert len(fps) == 1
        assert fps[0]["name"] == "test_foo"
        assert fps[0]["file"] == f
        assert fps[0]["class_name"] is None or fps[0]["class_name"] == ""
        assert len(fps[0]["body_hash"]) == 16
        assert len(fps[0]["ast_hash"]) > 0

    def test_multiple_functions(self, tmp_path):
        f = _write_test_file(
            tmp_path / "test_a.py",
            """\
            def test_one():
                assert True

            def test_two():
                x = 1 + 2
                assert x == 3
            """,
        )
        fps = _build_test_fingerprints([f])
        assert len(fps) == 2
        names = {fp["name"] for fp in fps}
        assert names == {"test_one", "test_two"}

    def test_class_method(self, tmp_path):
        f = _write_test_file(
            tmp_path / "test_a.py",
            """\
            class TestFoo:
                def test_bar(self):
                    assert True
            """,
        )
        fps = _build_test_fingerprints([f])
        assert len(fps) == 1
        assert fps[0]["name"] == "test_bar"

    def test_nonexistent_file(self):
        fps = _build_test_fingerprints(["/nonexistent/test_x.py"])
        assert fps == []

    def test_syntax_error_file(self, tmp_path):
        f = _write_test_file(tmp_path / "test_bad.py", "def test_broken(:\n")
        fps = _build_test_fingerprints([f])
        assert fps == []

    def test_identical_bodies_same_hash(self, tmp_path):
        f1 = _write_test_file(
            tmp_path / "test_a.py",
            """\
            def test_foo():
                assert 1 == 1
            """,
        )
        f2 = _write_test_file(
            tmp_path / "test_b.py",
            """\
            def test_foo():
                assert 1 == 1
            """,
        )
        fps = _build_test_fingerprints([f1, f2])
        assert len(fps) == 2
        assert fps[0]["body_hash"] == fps[1]["body_hash"]


# ── _find_cross_file_duplicates ──────────────────────────────────


class TestFindCrossFileDuplicates:
    def _make_fps(self, tmp_path):
        """Two files with identical test_foo."""
        return [
            {
                "file": str(tmp_path / "test_a.py"),
                "name": "test_foo",
                "class_name": "",
                "line": 1,
                "body_hash": "abc123",
                "ast_hash": "def456",
                "ctx_hash": "ctx789",
            },
            {
                "file": str(tmp_path / "test_b.py"),
                "name": "test_foo",
                "class_name": "",
                "line": 1,
                "body_hash": "abc123",
                "ast_hash": "def456",
                "ctx_hash": "ctx789",
            },
        ]

    def test_finds_byte_identical(self, tmp_path):
        fps = self._make_fps(tmp_path)
        seen: set[str] = set()
        findings = _find_cross_file_duplicates(
            fps,
            "body_hash",
            str(tmp_path),
            seen,
            duplicate_type="byte_identical",
            severity="warning",
            confidence=0.95,
            message_verb="byte-identical",
        )
        assert len(findings) == 1
        assert findings[0].kind == "THYGIENE003"
        assert findings[0].confidence == 0.95
        assert "byte-identical" in findings[0].message

    def test_skips_same_file(self, tmp_path):
        fps = [
            {
                "file": str(tmp_path / "test_a.py"),
                "name": "test_foo",
                "class_name": "",
                "line": 1,
                "body_hash": "abc",
                "ast_hash": "def",
                "ctx_hash": "ctx",
            },
            {
                "file": str(tmp_path / "test_a.py"),
                "name": "test_foo",
                "class_name": "TestA",
                "line": 10,
                "body_hash": "abc",
                "ast_hash": "def",
                "ctx_hash": "ctx",
            },
        ]
        findings = _find_cross_file_duplicates(
            fps,
            "body_hash",
            str(tmp_path),
            set(),
            duplicate_type="byte_identical",
            severity="warning",
            confidence=0.95,
            message_verb="byte-identical",
        )
        assert len(findings) == 0

    def test_deduplicates_across_calls(self, tmp_path):
        fps = self._make_fps(tmp_path)
        seen: set[str] = set()
        # First call
        f1 = _find_cross_file_duplicates(
            fps,
            "body_hash",
            str(tmp_path),
            seen,
            duplicate_type="byte",
            severity="warning",
            confidence=0.95,
            message_verb="byte-identical",
        )
        # Second call with same seen set
        f2 = _find_cross_file_duplicates(
            fps,
            "body_hash",
            str(tmp_path),
            seen,
            duplicate_type="byte",
            severity="warning",
            confidence=0.95,
            message_verb="byte-identical",
        )
        assert len(f1) == 1
        assert len(f2) == 0  # Already seen

    def test_no_duplicates(self, tmp_path):
        fps = [
            {
                "file": str(tmp_path / "test_a.py"),
                "name": "test_foo",
                "class_name": "",
                "line": 1,
                "body_hash": "aaa",
                "ast_hash": "bbb",
                "ctx_hash": "ccc",
            },
            {
                "file": str(tmp_path / "test_b.py"),
                "name": "test_bar",
                "class_name": "",
                "line": 1,
                "body_hash": "ddd",
                "ast_hash": "eee",
                "ctx_hash": "fff",
            },
        ]
        findings = _find_cross_file_duplicates(
            fps,
            "body_hash",
            str(tmp_path),
            set(),
            duplicate_type="byte",
            severity="warning",
            confidence=0.95,
            message_verb="byte-identical",
        )
        assert findings == []

    def test_class_name_in_display(self, tmp_path):
        fps = [
            {
                "file": str(tmp_path / "test_a.py"),
                "name": "test_foo",
                "class_name": "TestSuite",
                "line": 1,
                "body_hash": "abc",
                "ast_hash": "def",
                "ctx_hash": "ctx",
            },
            {
                "file": str(tmp_path / "test_b.py"),
                "name": "test_foo",
                "class_name": "TestSuite",
                "line": 1,
                "body_hash": "abc",
                "ast_hash": "def",
                "ctx_hash": "ctx",
            },
        ]
        findings = _find_cross_file_duplicates(
            fps,
            "body_hash",
            str(tmp_path),
            set(),
            duplicate_type="byte",
            severity="warning",
            confidence=0.95,
            message_verb="byte-identical",
        )
        assert len(findings) == 1
        assert "TestSuite.test_foo" in findings[0].message


# ── _add_subsumption_findings ────────────────────────────────────


class TestAddSubsumptionFindings:
    def test_subsumed_file_detected(self, tmp_path):
        # file_a has 1 test, file_b has 2 tests including file_a's
        file_a = str(tmp_path / "test_a.py")
        file_b = str(tmp_path / "test_b.py")
        fps = [
            {"file": file_a, "name": "test_foo", "body_hash": "abc", "ctx_hash": "ctx"},
            {"file": file_b, "name": "test_foo", "body_hash": "abc", "ctx_hash": "ctx"},
            {"file": file_b, "name": "test_bar", "body_hash": "def", "ctx_hash": "ctx"},
        ]
        findings: list[LintIssue] = []
        repairs: list[RepairAction] = []
        _add_subsumption_findings(fps, [file_a, file_b], str(tmp_path), findings, repairs)
        assert len(findings) == 1
        assert findings[0].kind == "THYGIENE005"
        assert len(repairs) == 1
        assert repairs[0].safe is True

    def test_no_subsumption_when_equal_size(self, tmp_path):
        file_a = str(tmp_path / "test_a.py")
        file_b = str(tmp_path / "test_b.py")
        fps = [
            {"file": file_a, "name": "test_foo", "body_hash": "abc", "ctx_hash": "ctx"},
            {"file": file_b, "name": "test_foo", "body_hash": "abc", "ctx_hash": "ctx"},
        ]
        findings: list[LintIssue] = []
        repairs: list[RepairAction] = []
        _add_subsumption_findings(fps, [file_a, file_b], str(tmp_path), findings, repairs)
        assert findings == []

    def test_no_subsumption_different_tests(self, tmp_path):
        file_a = str(tmp_path / "test_a.py")
        file_b = str(tmp_path / "test_b.py")
        fps = [
            {"file": file_a, "name": "test_foo", "body_hash": "abc", "ctx_hash": "ctx"},
            {"file": file_b, "name": "test_bar", "body_hash": "def", "ctx_hash": "ctx"},
            {"file": file_b, "name": "test_baz", "body_hash": "ghi", "ctx_hash": "ctx"},
        ]
        findings: list[LintIssue] = []
        repairs: list[RepairAction] = []
        _add_subsumption_findings(fps, [file_a, file_b], str(tmp_path), findings, repairs)
        assert findings == []


# ── _thygiene003_duplicates (integration) ────────────────────────


class TestThygiene003Integration:
    def test_finds_duplicates_across_files(self, tmp_path):
        f1 = _write_test_file(
            tmp_path / "test_a.py",
            """\
            def test_dup():
                x = 42
                assert x == 42
            """,
        )
        f2 = _write_test_file(
            tmp_path / "test_b.py",
            """\
            def test_dup():
                x = 42
                assert x == 42
            """,
        )
        findings, repairs = _thygiene003_duplicates([f1, f2], str(tmp_path))
        assert len(findings) >= 1
        assert any(f.kind == "THYGIENE003" for f in findings)

    def test_no_findings_for_unique_tests(self, tmp_path):
        f1 = _write_test_file(
            tmp_path / "test_a.py",
            """\
            def test_one():
                assert 1 == 1
            """,
        )
        f2 = _write_test_file(
            tmp_path / "test_b.py",
            """\
            def test_two():
                assert 2 == 2
            """,
        )
        findings, repairs = _thygiene003_duplicates([f1, f2], str(tmp_path))
        assert findings == []

    def test_subsumption_detected(self, tmp_path):
        f1 = _write_test_file(
            tmp_path / "test_small.py",
            """\
            def test_dup():
                assert True
            """,
        )
        f2 = _write_test_file(
            tmp_path / "test_big.py",
            """\
            def test_dup():
                assert True

            def test_extra():
                assert 2 == 2
            """,
        )
        findings, _ = _thygiene003_duplicates([f1, f2], str(tmp_path))
        kinds = {f.kind for f in findings}
        assert "THYGIENE005" in kinds or "THYGIENE003" in kinds

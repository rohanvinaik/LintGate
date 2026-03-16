"""Tests for lintgate.channels._test_hygiene_duplicates — duplicate detection."""

from __future__ import annotations

import textwrap

from lintgate.channels._test_hygiene_duplicates import (
    _add_subsumption_findings,
    _build_test_fingerprints,
    _find_cross_file_duplicates,
    _thygiene003_duplicates,
)
from lintgate.types import LintIssue  # noqa: TC001

# ── _build_test_fingerprints ─────────────────────────────────────────


class TestBuildTestFingerprints:
    """Tests for fingerprint construction from test files."""

    def test_single_file_single_function(self, tmp_path) -> None:
        f = tmp_path / "test_a.py"
        f.write_text(
            textwrap.dedent("""\
            def test_hello():
                assert 1 == 1
        """)
        )
        fps = _build_test_fingerprints([str(f)])
        assert len(fps) == 1
        assert fps[0]["name"] == "test_hello"
        assert fps[0]["file"] == str(f)
        assert fps[0]["class_name"] is None
        assert isinstance(fps[0]["body_hash"], str)
        assert len(fps[0]["body_hash"]) == 16

    def test_multiple_functions(self, tmp_path) -> None:
        f = tmp_path / "test_b.py"
        f.write_text(
            textwrap.dedent("""\
            def test_one():
                assert True

            def test_two():
                assert False
        """)
        )
        fps = _build_test_fingerprints([str(f)])
        assert len(fps) == 2
        names = {fp["name"] for fp in fps}
        assert names == {"test_one", "test_two"}

    def test_nonexistent_file_skipped(self, tmp_path) -> None:
        fps = _build_test_fingerprints([str(tmp_path / "nonexistent.py")])
        assert fps == []


# ── _find_cross_file_duplicates ──────────────────────────────────────


class TestFindCrossFileDuplicates:
    """Tests for cross-file duplicate detection by hash field."""

    def test_same_body_different_files(self, tmp_path) -> None:
        f1 = tmp_path / "test_a.py"
        f2 = tmp_path / "test_b.py"
        body = textwrap.dedent("""\
            def test_dup():
                assert 1 + 1 == 2
        """)
        f1.write_text(body)
        f2.write_text(body)

        fps = _build_test_fingerprints([str(f1), str(f2)])
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
        assert findings[0].severity == "warning"
        assert findings[0].confidence == 0.95

    def test_no_duplicates(self, tmp_path) -> None:
        f1 = tmp_path / "test_a.py"
        f2 = tmp_path / "test_b.py"
        f1.write_text("def test_one():\n    assert 1 == 1\n")
        f2.write_text("def test_two():\n    assert 2 == 2\n")

        fps = _build_test_fingerprints([str(f1), str(f2)])
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
        assert findings == []

    def test_seen_dupes_prevents_reemission(self, tmp_path) -> None:
        f1 = tmp_path / "test_a.py"
        f2 = tmp_path / "test_b.py"
        body = "def test_dup():\n    assert True\n"
        f1.write_text(body)
        f2.write_text(body)

        fps = _build_test_fingerprints([str(f1), str(f2)])
        seen: set[str] = set()
        findings1 = _find_cross_file_duplicates(
            fps,
            "body_hash",
            str(tmp_path),
            seen,
            duplicate_type="byte_identical",
            severity="warning",
            confidence=0.95,
            message_verb="byte-identical",
        )
        findings2 = _find_cross_file_duplicates(
            fps,
            "body_hash",
            str(tmp_path),
            seen,
            duplicate_type="byte_identical",
            severity="warning",
            confidence=0.95,
            message_verb="byte-identical",
        )
        assert len(findings1) == 1
        assert findings2 == []


# ── _add_subsumption_findings ────────────────────────────────────────


class TestAddSubsumptionFindings:
    """Tests for THYGIENE005 fully-subsumed file detection."""

    def test_subsumed_file_detected(self, tmp_path) -> None:
        f_small = tmp_path / "test_small.py"
        f_big = tmp_path / "test_big.py"
        f_small.write_text("def test_dup():\n    assert True\n")
        f_big.write_text(
            "def test_dup():\n    assert True\n\ndef test_extra():\n    assert 2 == 2\n"
        )

        fps = _build_test_fingerprints([str(f_small), str(f_big)])
        findings: list[LintIssue] = []
        repairs: list = []
        _add_subsumption_findings(fps, [str(f_small), str(f_big)], str(tmp_path), findings, repairs)
        assert len(findings) == 1
        assert findings[0].kind == "THYGIENE005"
        assert "test_small.py" in findings[0].message
        assert len(repairs) == 1
        assert repairs[0].kind == "safe_delete"

    def test_no_subsumption_when_same_size(self, tmp_path) -> None:
        f1 = tmp_path / "test_a.py"
        f2 = tmp_path / "test_b.py"
        body = "def test_dup():\n    assert True\n"
        f1.write_text(body)
        f2.write_text(body)

        fps = _build_test_fingerprints([str(f1), str(f2)])
        findings: list[LintIssue] = []
        repairs: list = []
        _add_subsumption_findings(fps, [str(f1), str(f2)], str(tmp_path), findings, repairs)
        # Same count means no subsumption (only checks when other has MORE tests)
        assert findings == []


# ── _thygiene003_duplicates (integration) ────────────────────────────


class TestThygiene003Duplicates:
    """Integration test for the full duplicate detection pipeline."""

    def test_empty_input(self) -> None:
        findings, repairs = _thygiene003_duplicates([], "/tmp")
        assert findings == []
        assert repairs == []

    def test_byte_identical_detected(self, tmp_path) -> None:
        f1 = tmp_path / "test_a.py"
        f2 = tmp_path / "test_b.py"
        body = "def test_x():\n    assert 42 == 42\n"
        f1.write_text(body)
        f2.write_text(body)
        findings, repairs = _thygiene003_duplicates([str(f1), str(f2)], str(tmp_path))
        assert len(findings) >= 1
        assert any(f.kind == "THYGIENE003" for f in findings)

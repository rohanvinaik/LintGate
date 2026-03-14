"""Tests for platonic generation helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lintgate.testing.platonic_generation import _has_nontrivial_tests, generate_tests


def _make_target(func_key: str = "mod.py::func", test_file: str = "tests/test_mod.py"):
    target = MagicMock()
    target.evidence.function_key = func_key
    target.target_test_file = test_file
    return target


class TestGenerateTests:
    def test_empty_targets_returns_zero(self, tmp_path):
        result = generate_tests(str(tmp_path), "mod.py", [])
        assert result["files_written"] == 0

    def test_successful_generation_returns_metadata(self, tmp_path):
        target = _make_target()
        mock_result = MagicMock()
        mock_result.target_test_file = "tests/test_mod.py"
        mock_result.content = "# test content"
        mock_result.functions_covered = ["func"]
        mock_result.enrichment_sources = ["source"]
        mock_result.manual_contract_candidates = []
        with patch("lintgate.testing.batch_regenerator.BatchRegenerator") as mock_reg:
            mock_reg.return_value.generate_for_file.return_value = mock_result
            result = generate_tests(str(tmp_path), "mod.py", [target])
        assert result["files_written"] == 1
        assert result["target"] == "tests/test_mod.py"
        assert result["functions_covered"] == ["func"]

    def test_none_result_returns_zero(self, tmp_path):
        target = _make_target()
        with patch("lintgate.testing.batch_regenerator.BatchRegenerator") as mock_reg:
            mock_reg.return_value.generate_for_file.return_value = None
            result = generate_tests(str(tmp_path), "mod.py", [target])
        assert result["files_written"] == 0

    def test_import_error_returns_structured_error(self, tmp_path):
        target = _make_target()
        with patch(
            "lintgate.testing.batch_regenerator.BatchRegenerator",
            side_effect=ImportError("no module"),
        ):
            result = generate_tests(str(tmp_path), "mod.py", [target])
        assert result["files_written"] == 0
        assert "generation_failed" in result.get("error", "")

    def test_write_error_returns_structured_error(self, tmp_path):
        target = _make_target()
        mock_result = MagicMock()
        mock_result.target_test_file = "tests/test_mod.py"
        mock_result.content = "# test"
        with patch("lintgate.testing.batch_regenerator.BatchRegenerator") as mock_reg:
            mock_reg.return_value.generate_for_file.return_value = mock_result
            with patch("builtins.open", side_effect=OSError("disk full")):
                result = generate_tests(str(tmp_path), "mod.py", [target])
        assert result["files_written"] == 0
        assert "write_failed" in result.get("error", "")

    def test_writes_to_correct_path(self, tmp_path):
        target = _make_target()
        mock_result = MagicMock()
        mock_result.target_test_file = "tests/test_mod.py"
        mock_result.content = "# test content"
        mock_result.functions_covered = ["func"]
        mock_result.enrichment_sources = []
        mock_result.manual_contract_candidates = []
        with patch("lintgate.testing.batch_regenerator.BatchRegenerator") as mock_reg:
            mock_reg.return_value.generate_for_file.return_value = mock_result
            generate_tests(str(tmp_path), "mod.py", [target])
        expected = tmp_path / "tests" / "test_mod.py"
        assert expected.exists()
        assert expected.read_text() == "# test content"

    def test_skips_existing_nontrivial_returns_canonical_path(self, tmp_path):
        """When canonical target has nontrivial tests, skip includes canonical_path."""
        target = _make_target()
        mock_result = MagicMock()
        mock_result.target_test_file = "tests/test_mod.py"
        # Create canonical file with real tests
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_mod.py").write_text("def test_real():\n    assert 1 + 1 == 2\n")
        staging = tmp_path / "staging"
        staging.mkdir()
        with patch("lintgate.testing.batch_regenerator.BatchRegenerator") as mock_reg:
            mock_reg.return_value.generate_for_file.return_value = mock_result
            result = generate_tests(str(tmp_path), "mod.py", [target], staging_dir=str(staging))
        assert result["files_written"] == 0
        assert result["skipped_reason"] == "existing_tests_adequate"
        assert result["canonical_path"] == str(test_dir / "test_mod.py")

    def test_staging_writes_with_content_hash(self, tmp_path):
        """Staged generation returns content_hash and staging_path."""
        target = _make_target()
        mock_result = MagicMock()
        mock_result.target_test_file = "tests/test_mod.py"
        mock_result.content = "def test_x():\n    assert True\n"
        mock_result.functions_covered = ["func"]
        mock_result.enrichment_sources = []
        mock_result.manual_contract_candidates = []
        staging = tmp_path / "staging"
        staging.mkdir()
        with patch("lintgate.testing.batch_regenerator.BatchRegenerator") as mock_reg:
            mock_reg.return_value.generate_for_file.return_value = mock_result
            result = generate_tests(str(tmp_path), "mod.py", [target], staging_dir=str(staging))
        assert result["files_written"] == 1
        assert result["staging_path"].startswith(str(staging))
        assert len(result["content_hash"]) == 64  # sha256 hex


class TestHasNontrivialTests:
    def test_real_assertion(self, tmp_path):
        f = tmp_path / "test_mod.py"
        f.write_text("def test_real():\n    assert 1 + 1 == 2\n")
        assert _has_nontrivial_tests(str(f)) is True

    def test_pytest_raises(self, tmp_path):
        f = tmp_path / "test_mod.py"
        f.write_text(
            "import pytest\ndef test_err():\n    with pytest.raises(ValueError):\n        pass\n"
        )
        assert _has_nontrivial_tests(str(f)) is True

    def test_stub_only(self, tmp_path):
        f = tmp_path / "test_mod.py"
        f.write_text("def test_stub():\n    assert True\n")
        assert _has_nontrivial_tests(str(f)) is False

    def test_nonexistent_file(self):
        assert _has_nontrivial_tests("/nonexistent/test.py") is False

    def test_empty_file(self, tmp_path):
        f = tmp_path / "test_mod.py"
        f.write_text("")
        assert _has_nontrivial_tests(str(f)) is False

    def test_helper_assert_call(self, tmp_path):
        f = tmp_path / "test_mod.py"
        f.write_text("def test_x():\n    assert_equal(1, 1)\n")
        assert _has_nontrivial_tests(str(f)) is True

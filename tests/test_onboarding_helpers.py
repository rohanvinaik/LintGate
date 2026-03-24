"""Tests for helper functions extracted from onboarding_tools.py.

Covers: _parse_pyproject_metadata, _detect_python_version_fallback,
_detect_license_fallback, _scan_project_dirs, _detect_project_layout,
_apply_managed_artifact, _read_informational_bandit_codes,
_compute_bandit_ci_skips, and register.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

from mcp_tools.onboarding_tools import (
    register,
)
from mcp_tools.quality.discovery import (
    _detect_license_fallback,
    _detect_python_version_fallback,
    _parse_pyproject_metadata,
    _scan_project_dirs,
)
from mcp_tools.quality_helpers import (
    _apply_managed_artifact,
    _compute_bandit_ci_skips,
    _detect_project_layout,
    _read_informational_bandit_codes,
)

def _load_tool_result(json_str):
    import json as _j, os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f: return _j.loads(f.read())
    return r


# -- _parse_pyproject_metadata ------------------------------------------------


class TestParsePyprojectMetadata:
    """Tests for _parse_pyproject_metadata."""

    def test_valid_pyproject_extracts_version(self, tmp_path: Path) -> None:
        """Extracts requires-python version from valid pyproject.toml."""
        (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.11"\n')
        version, _lic, _dirs, has = _parse_pyproject_metadata(tmp_path)
        assert version == "3.11"
        assert has is True

    def test_valid_pyproject_extracts_license_dict(self, tmp_path: Path) -> None:
        """Extracts license from dict form (text key)."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.12"\n\n[project.license]\ntext = "MIT"\n'
        )
        _ver, lic, _dirs, _has = _parse_pyproject_metadata(tmp_path)
        assert lic == "MIT"

    def test_valid_pyproject_extracts_license_string(self, tmp_path: Path) -> None:
        """Extracts license when specified as a bare string."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.12"\nlicense = "Apache-2.0"\n'
        )
        _ver, lic, _dirs, _has = _parse_pyproject_metadata(tmp_path)
        assert lic == "Apache-2.0"

    def test_missing_pyproject(self, tmp_path: Path) -> None:
        """Returns defaults when pyproject.toml is absent."""
        version, lic, dirs, has = _parse_pyproject_metadata(tmp_path)
        assert version == "3"
        assert lic is None
        assert dirs == []
        assert has is False

    def test_malformed_toml(self, tmp_path: Path) -> None:
        """Returns fallback when pyproject.toml is not valid TOML."""
        (tmp_path / "pyproject.toml").write_text("{{{{not valid toml")
        version, lic, dirs, has = _parse_pyproject_metadata(tmp_path)
        assert version == "3"
        assert lic is None
        assert dirs == []
        assert has is True

    def test_missing_project_keys(self, tmp_path: Path) -> None:
        """Handles pyproject.toml without project section gracefully."""
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
        version, lic, dirs, has = _parse_pyproject_metadata(tmp_path)
        assert version == "3"
        assert lic is None
        assert dirs == []
        assert has is True

    def test_extracts_testpaths(self, tmp_path: Path) -> None:
        """Extracts pytest testpaths from pyproject.toml."""
        (tmp_path / "pyproject.toml").write_text(
            '[tool.pytest.ini_options]\ntestpaths = ["tests", "integration"]\n'
        )
        _ver, _lic, dirs, _has = _parse_pyproject_metadata(tmp_path)
        assert dirs == ["tests", "integration"]

    def test_non_dict_non_string_license(self, tmp_path: Path) -> None:
        """Returns None when license is neither dict nor string (else branch)."""
        (tmp_path / "pyproject.toml").write_bytes(b"[project]\nlicense = 42\n")
        _ver, lic, _dirs, _has = _parse_pyproject_metadata(tmp_path)
        assert lic is None

    def test_license_dict_file_key(self, tmp_path: Path) -> None:
        """Extracts license from dict form using 'file' key when 'text' absent."""
        (tmp_path / "pyproject.toml").write_bytes(b'[project.license]\nfile = "LICENSE.txt"\n')
        _ver, lic, _dirs, _has = _parse_pyproject_metadata(tmp_path)
        assert lic == "LICENSE.txt"

    def test_tomllib_fallback_to_tomli(self, tmp_path: Path) -> None:
        """Lines 529-530: when tomllib import fails, falls back to tomli."""
        import sys

        (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.10"\n')
        # Temporarily make 'tomllib' unavailable by removing it from sys.modules
        # and patching builtins.__import__ to raise for 'tomllib'
        original_import = (
            __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__
        )

        def _mock_import(name, *args, **kwargs):
            if name == "tomllib":
                raise ModuleNotFoundError("mocked: no tomllib")
            return original_import(name, *args, **kwargs)

        saved = sys.modules.pop("tomllib", None)
        try:
            with patch("builtins.__import__", side_effect=_mock_import):
                # Re-import the function to trigger the fallback code path
                # Since the import is inside the function, we just call it
                version, _lic, _dirs, has = _parse_pyproject_metadata(tmp_path)
            # tomli should have been used as fallback — verify the parse succeeded
            assert version == "3.10"
            assert has is True
        finally:
            if saved is not None:
                sys.modules["tomllib"] = saved


# -- _detect_python_version_fallback -----------------------------------------


class TestDetectPythonVersionFallback:
    """Tests for _detect_python_version_fallback."""

    def test_reads_python_version_file(self, tmp_path: Path) -> None:
        """Extracts version from .python-version file."""
        (tmp_path / ".python-version").write_text("3.12.1\n")
        assert _detect_python_version_fallback(tmp_path) == "3.12"

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        """Returns None when .python-version is absent."""
        assert _detect_python_version_fallback(tmp_path) is None

    def test_non_version_content_returns_none(self, tmp_path: Path) -> None:
        """Returns None when .python-version contains no version pattern."""
        (tmp_path / ".python-version").write_text("pypy\n")
        assert _detect_python_version_fallback(tmp_path) is None

    def test_oserror_returns_none(self, tmp_path: Path) -> None:
        """Returns None when .python-version triggers OSError on read."""
        pv = tmp_path / ".python-version"
        pv.write_text("3.11\n")
        with patch("pathlib.Path.read_text", side_effect=OSError("perm denied")):
            assert _detect_python_version_fallback(tmp_path) is None


# -- _detect_license_fallback -------------------------------------------------


class TestDetectLicenseFallback:
    """Tests for _detect_license_fallback."""

    def test_detects_mit(self, tmp_path: Path) -> None:
        """Detects MIT license from LICENSE file content."""
        (tmp_path / "LICENSE").write_text("MIT License\n\nCopyright 2026\n")
        assert _detect_license_fallback(tmp_path) == "MIT"

    def test_detects_apache(self, tmp_path: Path) -> None:
        """Detects Apache license from LICENSE file content."""
        (tmp_path / "LICENSE").write_text("Apache License, Version 2.0\n")
        assert _detect_license_fallback(tmp_path) == "Apache-2.0"

    def test_detects_gpl(self, tmp_path: Path) -> None:
        """Detects GPL license from LICENSE file content."""
        (tmp_path / "LICENSE").write_text("GNU GENERAL PUBLIC LICENSE\nVersion 3\n")
        assert _detect_license_fallback(tmp_path) == "GPL-3.0"

    def test_detects_bsd(self, tmp_path: Path) -> None:
        """Detects BSD license from LICENSE file content."""
        (tmp_path / "LICENSE").write_text("BSD 3-Clause License\n")
        assert _detect_license_fallback(tmp_path) == "BSD-3-Clause"

    def test_no_license_file(self, tmp_path: Path) -> None:
        """Returns None when no LICENSE file exists."""
        assert _detect_license_fallback(tmp_path) is None

    def test_unrecognized_license(self, tmp_path: Path) -> None:
        """Returns None when LICENSE content matches no known license."""
        (tmp_path / "LICENSE").write_text("Custom license terms apply.\n")
        assert _detect_license_fallback(tmp_path) is None

    def test_license_txt_variant(self, tmp_path: Path) -> None:
        """Detects license from LICENSE.txt variant."""
        (tmp_path / "LICENSE.txt").write_text("MIT License\n")
        assert _detect_license_fallback(tmp_path) == "MIT"

    def test_licence_spelling(self, tmp_path: Path) -> None:
        """Detects license from LICENCE (British spelling)."""
        (tmp_path / "LICENCE").write_text("MIT License\n")
        assert _detect_license_fallback(tmp_path) == "MIT"

    def test_oserror_on_read_returns_none(self, tmp_path: Path) -> None:
        """Returns None when LICENSE file triggers OSError on read."""
        (tmp_path / "LICENSE").write_text("MIT License\n")
        with patch("pathlib.Path.read_text", side_effect=OSError("read fail")):
            assert _detect_license_fallback(tmp_path) is None


# -- _scan_project_dirs -------------------------------------------------------


class TestScanProjectDirs:
    """Tests for _scan_project_dirs."""

    def test_finds_source_package(self, tmp_path: Path) -> None:
        """Detects Python package directories with __init__.py."""
        pkg = tmp_path / "mypackage"
        pkg.mkdir()
        (pkg / "__init__.py").touch()
        src, test, doc, _trunc = _scan_project_dirs(tmp_path, [])
        assert "mypackage" in src

    def test_finds_test_dir(self, tmp_path: Path) -> None:
        """Detects tests/ directory."""
        (tmp_path / "tests").mkdir()
        _src, test, _doc, _trunc = _scan_project_dirs(tmp_path, [])
        assert "tests" in test

    def test_finds_doc_dir(self, tmp_path: Path) -> None:
        """Detects docs/ directory."""
        (tmp_path / "docs").mkdir()
        _src, _test, doc, _trunc = _scan_project_dirs(tmp_path, [])
        assert "docs" in doc

    def test_skips_venv(self, tmp_path: Path) -> None:
        """Skips .venv and venv directories."""
        (tmp_path / ".venv").mkdir()
        (tmp_path / "venv").mkdir()
        src, test, doc, _trunc = _scan_project_dirs(tmp_path, [])
        assert ".venv" not in src
        assert "venv" not in src

    def test_skips_hidden_dirs(self, tmp_path: Path) -> None:
        """Skips dot-prefixed directories."""
        (tmp_path / ".hidden").mkdir()
        src, _test, _doc, _trunc = _scan_project_dirs(tmp_path, [])
        assert ".hidden" not in src

    def test_src_layout(self, tmp_path: Path) -> None:
        """Detects src/ layout with nested packages."""
        pkg = tmp_path / "src" / "mylib"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").touch()
        src, _test, _doc, _trunc = _scan_project_dirs(tmp_path, [])
        assert "src/mylib" in src

    def test_does_not_override_pyproject_test_dirs(self, tmp_path: Path) -> None:
        """When test_dirs already provided, does not re-add tests/."""
        (tmp_path / "tests").mkdir()
        _src, test, _doc, _trunc = _scan_project_dirs(tmp_path, ["custom_tests"])
        assert test == ["custom_tests"]

    def test_src_subdir_without_init(self, tmp_path: Path) -> None:
        """Branch 618->617: src/ sub-dir without __init__.py is not a package."""
        src_dir = tmp_path / "src" / "data"
        src_dir.mkdir(parents=True)
        (src_dir / "README.md").touch()
        src, _test, _doc, _trunc = _scan_project_dirs(tmp_path, [])
        assert all("data" not in s for s in src)

    def test_regular_file_at_root_skipped(self, tmp_path: Path) -> None:
        """Branch 607: non-directory entry at root is skipped by is_dir() check."""
        (tmp_path / "setup.py").write_text("# setup")
        (tmp_path / "mypackage").mkdir()
        (tmp_path / "mypackage" / "__init__.py").touch()
        src, test, doc, _trunc = _scan_project_dirs(tmp_path, [])
        # setup.py should not appear anywhere
        assert "setup.py" not in src
        assert "mypackage" in src


# -- _detect_project_layout ---------------------------------------------------


class TestDetectProjectLayoutIntegration:
    """Integration tests for _detect_project_layout."""

    def test_full_project(self, tmp_path: Path) -> None:
        """Detect layout from a project with pyproject, package, tests, docs."""
        (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.11"\n')
        pkg = tmp_path / "mylib"
        pkg.mkdir()
        (pkg / "__init__.py").touch()
        (tmp_path / "tests").mkdir()
        (tmp_path / "docs").mkdir()
        (tmp_path / "LICENSE").write_text("MIT License\n")

        result = _detect_project_layout(str(tmp_path))

        assert "mylib" in result["source_dirs"]
        assert "tests" in result["test_dirs"]
        assert "docs" in result["doc_dirs"]
        assert result["python_version"] == "3.11"
        assert result["license"] == "MIT"
        assert result["has_pyproject_toml"] is True

    def test_empty_project(self, tmp_path: Path) -> None:
        """Empty project gets sensible defaults."""
        result = _detect_project_layout(str(tmp_path))
        assert result["source_dirs"] == ["."]
        assert result["python_version"] == "3"
        assert result["license"] is None
        assert result["has_pyproject_toml"] is False

    def test_python_version_fallback_to_dotfile(self, tmp_path: Path) -> None:
        """Falls back to .python-version when pyproject has no version."""
        (tmp_path / ".python-version").write_text("3.10.5\n")
        result = _detect_project_layout(str(tmp_path))
        assert result["python_version"] == "3.10"

    def test_license_from_pyproject_skips_fallback(self, tmp_path: Path) -> None:
        """Branch 632->635: license_id already set skips _detect_license_fallback."""
        (tmp_path / "pyproject.toml").write_bytes(
            b'[project]\nrequires-python = ">=3.11"\nlicense = "BSD-2-Clause"\n'
        )
        # LICENSE file has MIT, but pyproject license should take precedence
        (tmp_path / "LICENSE").write_text("MIT License\n")
        result = _detect_project_layout(str(tmp_path))
        assert result["license"] == "BSD-2-Clause"


# -- _apply_managed_artifact --------------------------------------------------


class TestApplyManagedArtifact:
    """Tests for _apply_managed_artifact."""

    def test_write_new_file(self, tmp_path: Path) -> None:
        """Creates file when it does not exist and write=True."""
        path = str(tmp_path / "artifact.yml")
        result = _apply_managed_artifact(path, "content here", exists=False, write=True)
        assert result["status"] == "written"
        assert (tmp_path / "artifact.yml").read_text() == "content here"

    def test_preview_new_file(self, tmp_path: Path) -> None:
        """Previews content when file does not exist and write=False."""
        path = str(tmp_path / "artifact.yml")
        result = _apply_managed_artifact(path, "content here", exists=False, write=False)
        assert result["status"] == "preview"
        assert result["content"] == "content here"
        assert not (tmp_path / "artifact.yml").exists()

    def test_already_exists_identical(self, tmp_path: Path) -> None:
        """Reports already_exists when content hash matches."""
        fpath = tmp_path / "artifact.yml"
        fpath.write_text("content here")
        result = _apply_managed_artifact(str(fpath), "content here", exists=True, write=True)
        assert result["status"] == "already_exists"

    def test_drift_repaired(self, tmp_path: Path) -> None:
        """Detects drift but does NOT overwrite content when write=True to preserve user config."""
        fpath = tmp_path / "artifact.yml"
        fpath.write_text("old content")
        result = _apply_managed_artifact(str(fpath), "new content", exists=True, write=True)
        assert result["status"] == "drift_repaired"
        assert "previous_hash" in result
        assert "new_hash" in result
        assert fpath.read_text() == "old content"

    def test_outdated_without_write(self, tmp_path: Path) -> None:
        """Reports outdated status when content differs and write=False."""
        fpath = tmp_path / "artifact.yml"
        fpath.write_text("old content")
        result = _apply_managed_artifact(str(fpath), "new content", exists=True, write=False)
        assert result["status"] == "outdated"
        assert "current_hash" in result
        assert "expected_hash" in result
        assert fpath.read_text() == "old content"

    def test_oserror_on_read(self, tmp_path: Path) -> None:
        """Gracefully handles OSError when reading existing file."""
        # Point at a directory instead of a file to trigger OSError
        dir_path = tmp_path / "dir_as_file"
        dir_path.mkdir()
        result = _apply_managed_artifact(str(dir_path), "content", exists=True, write=False)
        # empty string hashed vs "content" hashed -> outdated
        expected_hash = hashlib.sha256(b"content").hexdigest()[:16]
        empty_hash = hashlib.sha256(b"").hexdigest()[:16]
        assert result["status"] == "outdated"
        assert result["current_hash"] == empty_hash
        assert result["expected_hash"] == expected_hash


# -- _read_informational_bandit_codes -----------------------------------------


class TestReadInformationalBanditCodes:
    """Tests for _read_informational_bandit_codes."""

    def test_returns_hardcoded_codes(self) -> None:
        """Returns hardcoded list of informational bandit codes."""
        codes = _read_informational_bandit_codes()
        assert "B101" in codes
        assert "B108" in codes
        assert "B311" in codes
        assert "B404" in codes
        assert "B603" in codes
        assert "B607" in codes

    def test_returns_list(self) -> None:
        """Returns a list type."""
        codes = _read_informational_bandit_codes()
        assert isinstance(codes, list)
        assert len(codes) == 6

    def test_no_args_accepted(self) -> None:
        """Function takes no positional arguments."""
        codes = _read_informational_bandit_codes()
        assert len(codes) > 0

    def test_all_codes_start_with_b(self) -> None:
        """All returned codes start with 'B'."""
        codes = _read_informational_bandit_codes()
        assert all(c.startswith("B") for c in codes)

    def test_consistent_results(self) -> None:
        """Returns same results on repeated calls."""
        codes1 = _read_informational_bandit_codes()
        codes2 = _read_informational_bandit_codes()
        assert codes1 == codes2


# -- _compute_bandit_ci_skips -------------------------------------------------


class TestComputeBanditCiSkips:
    """Tests for _compute_bandit_ci_skips."""

    def test_default_skips(self, tmp_path: Path) -> None:
        """Returns comma-separated string with base codes."""
        skips = _compute_bandit_ci_skips(str(tmp_path))
        assert isinstance(skips, str)
        skip_list = skips.split(",")
        assert "B101" in skip_list
        assert "B108" in skip_list

    def test_includes_all_hardcoded_codes(self, tmp_path: Path) -> None:
        """Includes all hardcoded informational codes from _read_informational_bandit_codes."""
        skips = _compute_bandit_ci_skips(str(tmp_path))
        skip_list = skips.split(",")
        assert "B404" in skip_list
        assert "B603" in skip_list
        assert "B607" in skip_list
        assert "B101" in skip_list

    def test_result_is_comma_separated(self, tmp_path: Path) -> None:
        """Returns a comma-separated string."""
        skips = _compute_bandit_ci_skips(str(tmp_path))
        assert isinstance(skips, str)
        assert "," in skips

    def test_no_duplicates(self, tmp_path: Path) -> None:
        """Does not duplicate codes in the result."""
        skips = _compute_bandit_ci_skips(str(tmp_path))
        skip_list = skips.split(",")
        assert len(skip_list) == len(set(skip_list))

    def test_result_is_sorted(self, tmp_path: Path) -> None:
        """Skip list is returned sorted."""
        skips = _compute_bandit_ci_skips(str(tmp_path))
        skip_list = skips.split(",")
        assert skip_list == sorted(skip_list)


# -- register -----------------------------------------------------------------


class TestRegister:
    """Tests for the register function."""

    def test_returns_expected_keys(self) -> None:
        """register returns a dict with the expected tool keys."""
        mock_mcp = MagicMock()
        # mcp.tool() returns a decorator, so we need it to return the function
        mock_mcp.tool.return_value = lambda fn: fn
        mock_helpers = {
            "_validate_project_root": lambda p: p,
            "_build_onboarding_status": lambda p: {},
        }
        result = register(mock_mcp, mock_helpers)
        assert isinstance(result, dict)
        assert "getting_started" in result
        assert "scaffold_config" in result
        assert "setup_github_quality" in result
        assert callable(result["getting_started"])
        assert callable(result["scaffold_config"])
        assert callable(result["setup_github_quality"])

    def test_getting_started_reset_branch(self, tmp_path: Path) -> None:
        """Lines 2287-2288: reset=True triggers _reset_project_state."""
        mock_mcp = MagicMock()
        mock_mcp.tool.return_value = lambda fn: fn
        mock_helpers = {
            "_validate_project_root": MagicMock(return_value=str(tmp_path)),
            "_build_onboarding_status": MagicMock(
                return_value={
                    "config_state": "config_missing",
                }
            ),
        }
        tools = register(mock_mcp, mock_helpers)
        # Create config so auto_setup does not run scaffold
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

        with (
            patch(
                "mcp_tools.onboarding_tools._reset_project_state",
                return_value=[{"action": "reset_dir", "path": "/tmp/x"}],
            ) as mock_reset,
            patch(
                "mcp_tools.onboarding_tools._ensure_project_venv",
                return_value={"status": "exists"},
            ),
            patch(
                "mcp_tools.onboarding_tools._collect_external_tool_gaps",
                return_value={"missing_tools": []},
            ),
            patch(
                "mcp_tools.onboarding_tools._project_venv_python",
                return_value=None,
            ),
        ):
            raw = tools["getting_started"](path=str(tmp_path), reset=True)
            mock_reset.assert_called_once_with(str(tmp_path))
            output = _load_tool_result(raw)
            actions = output.get("startup_setup", {}).get("actions_applied", [])
            assert any(a.get("action") == "reset_dir" for a in actions)

    def test_scaffold_config_preview_existing(self, tmp_path: Path) -> None:
        """Branch: scaffold_config with existing config and write=False → preview_existing."""
        mock_mcp = MagicMock()
        mock_mcp.tool.return_value = lambda fn: fn
        mock_helpers = {
            "_validate_project_root": MagicMock(return_value=str(tmp_path)),
            "_build_onboarding_status": MagicMock(return_value={}),
        }
        tools = register(mock_mcp, mock_helpers)

        # Create existing config
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text("controlplane:\n  enabled: true\n")

        yaml_content = "controlplane:\n  enabled: true\n  new: true\n"
        with patch(
            "mcp_tools.onboarding_tools._scaffold_config_yaml",
            return_value=yaml_content,
        ):
            raw = tools["scaffold_config"](path=str(tmp_path), write=False)
        output = _load_tool_result(raw)
        assert output["status"] == "preview_existing"
        assert "already exists" in output.get("message", "")

    def test_scaffold_config_write_branch(self, tmp_path: Path) -> None:
        """Lines 2532-2534: scaffold_config with write=True writes file."""
        mock_mcp = MagicMock()
        mock_mcp.tool.return_value = lambda fn: fn
        mock_helpers = {
            "_validate_project_root": MagicMock(return_value=str(tmp_path)),
            "_build_onboarding_status": MagicMock(return_value={}),
        }
        tools = register(mock_mcp, mock_helpers)
        yaml_content = "controlplane:\n  enabled: true\n"
        with patch(
            "mcp_tools.onboarding_tools._scaffold_config_yaml",
            return_value=yaml_content,
        ):
            raw = tools["scaffold_config"](path=str(tmp_path), write=True)
        output = _load_tool_result(raw)
        assert output["status"] == "written"
        config_path = tmp_path / ".claude" / "lintgate.yaml"
        assert config_path.exists()
        assert config_path.read_text() == yaml_content


class TestParsePyprojectLicenseString:
    """Cover _parse_pyproject_metadata branch: license as plain string."""

    def test_license_string_value(self, tmp_path):
        from mcp_tools.quality.discovery import _parse_pyproject_metadata

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nlicense = "MIT"\n')
        _, license_id, _, has = _parse_pyproject_metadata(tmp_path)
        assert license_id == "MIT"
        assert has is True


class TestScanProjectDirsSrcLayout:
    """Cover _scan_project_dirs branch: src/ layout with sub-packages."""

    def test_src_layout_discovered(self, tmp_path):
        from mcp_tools.quality.discovery import _scan_project_dirs

        # Create src/mypkg/__init__.py
        pkg = tmp_path / "src" / "mypkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").touch()

        source_dirs, test_dirs, doc_dirs, _trunc = _scan_project_dirs(tmp_path, [])
        assert "src/mypkg" in source_dirs

    def test_src_subdir_without_init_skipped(self, tmp_path):
        from mcp_tools.quality.discovery import _scan_project_dirs

        # Create src/data/ (no __init__.py)
        (tmp_path / "src" / "data").mkdir(parents=True)

        source_dirs, _, _, _trunc = _scan_project_dirs(tmp_path, [])
        assert source_dirs == []


class TestRegisterGettingStartedBranch:
    """Cover register() branch: getting_started with existing CLAUDE.md."""

    def test_getting_started_returns_json(self, tmp_path):
        from mcp_tools.onboarding_tools import register

        mcp_mock = MagicMock()
        mcp_mock.tool.return_value = lambda fn: fn
        helpers = {
            "_validate_project_root": lambda p: str(tmp_path),
            "_build_onboarding_status": lambda p: {
                "has_config": True,
                "config_state": "config_enabled",
                "controlplane": "enabled",
            },
        }
        tools = register(mcp_mock, helpers)
        fn = tools["getting_started"]

        # Create minimal project layout
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "CLAUDE.md").write_text("# Existing\n")
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")

        raw = fn(path=str(tmp_path))
        output = _load_tool_result(raw)
        assert "project" in output or "guidance" in output or "status" in output

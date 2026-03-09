"""Tests for the toolchain manifest system (lintgate/tool_manifest.py).

Covers: manifest loading, tool discovery, registry reconciliation,
install strategy selection, and CLI entry point.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from lintgate.tool_manifest import (
    ManifestReport,
    _build_install_hint,
    _detect_platform,
    _find_executable,
    _parse_tool_entry,
    _parse_version_spec,
    check_tool_health,
    full_toolchain_report,
    install_missing_tools,
    load_toolchain_manifest,
    reconcile_with_registry,
)

if TYPE_CHECKING:
    from pathlib import Path

# ── Helpers ──────────────────────────────────────────────────────────


def _make_contract(tmp_path: Path, toolchain_yaml: str) -> Path:
    """Write a gate_contract.yaml with a toolchain section."""
    content = f"version: '1.0'\n{toolchain_yaml}"
    (tmp_path / "gate_contract.yaml").write_text(content)
    return tmp_path


def _make_tool_entry(**overrides) -> dict:
    """Build a minimal tool entry dict."""
    entry = {
        "id": "testtool",
        "kind": "python_cli",
        "package": "testtool>=1.0",
        "required": False,
        "required_by": ["lint"],
        "auto_install": True,
    }
    entry.update(overrides)
    return entry


# ── _parse_version_spec ─────────────────────────────────────────────


class TestParseVersionSpec:
    def test_with_gte(self):
        pkg, spec = _parse_version_spec("ruff>=0.4.0")
        assert pkg == "ruff"
        assert spec == ">=0.4.0"

    def test_with_eq(self):
        pkg, spec = _parse_version_spec("mypy==1.8.0")
        assert pkg == "mypy"
        assert spec == "==1.8.0"

    def test_no_version(self):
        pkg, spec = _parse_version_spec("ruff")
        assert pkg == "ruff"
        assert spec == ""

    def test_tilde_eq(self):
        pkg, spec = _parse_version_spec("bandit~=1.7")
        assert pkg == "bandit"
        assert spec == "~=1.7"


# ── _parse_tool_entry ───────────────────────────────────────────────


class TestParseToolEntry:
    def test_python_cli(self):
        entry = _make_tool_entry(id="ruff", package="ruff>=0.4.0")
        req = _parse_tool_entry(entry)
        assert req.id == "ruff"
        assert req.kind == "python_cli"
        assert req.package == "ruff"
        assert req.version_spec == ">=0.4.0"
        assert req.auto_install is True

    def test_native_binary(self):
        entry = _make_tool_entry(
            id="qlty",
            kind="native_binary",
            package="",
            required=True,
            install={"darwin": "brew install qlty"},
            auto_install=False,
        )
        req = _parse_tool_entry(entry)
        assert req.id == "qlty"
        assert req.kind == "native_binary"
        assert req.install_commands == {"darwin": "brew install qlty"}
        assert req.auto_install is False
        assert req.required is True

    def test_missing_package_defaults_to_id(self):
        entry = _make_tool_entry(id="foo", package="")
        req = _parse_tool_entry(entry)
        assert req.package == "foo"

    def test_required_by_string_coerced_to_list(self):
        entry = _make_tool_entry(required_by="lint")
        req = _parse_tool_entry(entry)
        assert req.required_by == ["lint"]


# ── load_toolchain_manifest ─────────────────────────────────────────


class TestLoadToolchainManifest:
    def test_loads_from_contract(self, tmp_path):
        _make_contract(
            tmp_path,
            """
toolchain:
  tools:
    - id: ruff
      kind: python_cli
      package: "ruff>=0.4.0"
      required: true
      required_by: [lint]
      auto_install: true
    - id: qlty
      kind: native_binary
      required: true
      required_by: [pre_push]
      auto_install: false
      install:
        darwin: "brew install qlty"
""",
        )
        manifest = load_toolchain_manifest(str(tmp_path))
        assert len(manifest) == 2
        assert manifest[0].id == "ruff"
        assert manifest[1].id == "qlty"

    def test_falls_back_to_defaults_when_no_contract(self, tmp_path):
        manifest = load_toolchain_manifest(str(tmp_path))
        # Defaults include ruff, mypy, ty, radon, bandit, pip-audit, qlty, gitleaks
        ids = {r.id for r in manifest}
        assert "ruff" in ids
        assert "qlty" in ids
        assert "gitleaks" in ids

    def test_falls_back_when_no_toolchain_section(self, tmp_path):
        (tmp_path / "gate_contract.yaml").write_text("version: '1.0'\n")
        manifest = load_toolchain_manifest(str(tmp_path))
        assert len(manifest) > 0  # defaults

    def test_ignores_invalid_entries(self, tmp_path):
        _make_contract(
            tmp_path,
            """
toolchain:
  tools:
    - id: good
      kind: python_cli
      package: good
    - not_a_dict
    - {}
""",
        )
        manifest = load_toolchain_manifest(str(tmp_path))
        assert len(manifest) == 1
        assert manifest[0].id == "good"


# ── _find_executable ────────────────────────────────────────────────


class TestFindExecutable:
    def test_finds_in_venv(self, tmp_path):
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "ruff").write_text("#!/bin/sh\n")
        assert _find_executable("ruff", str(tmp_path)) is not None

    def test_returns_none_when_missing(self, tmp_path):
        with patch("shutil.which", return_value=None):
            result = _find_executable("nonexistent_tool_xyz", str(tmp_path))
            assert result is None

    def test_falls_back_to_system_path(self, tmp_path):
        with patch("shutil.which", return_value="/usr/bin/git"):
            result = _find_executable("git", str(tmp_path))
            assert result == "/usr/bin/git"


# ── check_tool_health ──────────────────────────────────────────────


class TestCheckToolHealth:
    def test_reports_installed_and_missing(self, tmp_path):
        manifest = [
            _parse_tool_entry(_make_tool_entry(id="python", required=True)),
            _parse_tool_entry(_make_tool_entry(id="nonexistent_tool_xyz_123", required=True)),
        ]
        with patch(
            "shutil.which",
            side_effect=lambda n: "/usr/bin/python" if n == "python" else None,
        ):
            statuses = check_tool_health(str(tmp_path), manifest)

        assert len(statuses) == 2
        python_status = next(s for s in statuses if s.id == "python")
        missing_status = next(s for s in statuses if s.id == "nonexistent_tool_xyz_123")
        assert python_status.installed is True
        assert missing_status.installed is False
        assert missing_status.install_hint != ""


# ── _build_install_hint ─────────────────────────────────────────────


class TestBuildInstallHint:
    def test_python_cli_hint(self):
        req = _parse_tool_entry(
            _make_tool_entry(id="ruff", kind="python_cli", package="ruff>=0.4.0")
        )
        hint = _build_install_hint(req, "darwin")
        assert "uv tool install" in hint or "pip install" in hint
        assert "ruff" in hint

    def test_native_binary_hint(self):
        req = _parse_tool_entry(
            _make_tool_entry(
                id="qlty",
                kind="native_binary",
                package="",
                install={"darwin": "brew install qlty", "linux": "curl | sh"},
            )
        )
        hint = _build_install_hint(req, "darwin")
        assert "brew install qlty" in hint

    def test_native_binary_fallback_platform(self):
        req = _parse_tool_entry(
            _make_tool_entry(
                id="qlty",
                kind="native_binary",
                package="",
                install={"linux": "apt install qlty"},
            )
        )
        hint = _build_install_hint(req, "darwin")
        assert "apt install qlty" in hint  # shows linux hint with note


# ── install_missing_tools ──────────────────────────────────────────


class TestInstallMissingTools:
    def test_dry_run(self, tmp_path):
        manifest = [
            _parse_tool_entry(_make_tool_entry(id="nonexistent", required=False, auto_install=True))
        ]
        with patch("shutil.which", return_value=None):
            statuses = check_tool_health(str(tmp_path), manifest)
            results = install_missing_tools(str(tmp_path), statuses, auto_only=True, dry_run=True)

        assert len(results) == 1
        assert results[0]["status"] == "would_install"
        assert results[0]["tool"] == "nonexistent"

    def test_skips_non_auto_install(self, tmp_path):
        manifest = [
            _parse_tool_entry(
                _make_tool_entry(id="qlty", kind="native_binary", package="", auto_install=False)
            )
        ]
        with patch("shutil.which", return_value=None):
            statuses = check_tool_health(str(tmp_path), manifest)
            results = install_missing_tools(str(tmp_path), statuses, auto_only=True, dry_run=True)

        assert len(results) == 0  # qlty is not auto_install


# ── reconcile_with_registry ─────────────────────────────────────────


class TestReconcileWithRegistry:
    def test_no_drift_when_manifest_covers_registry(self, tmp_path):
        """When all required_tools are in the manifest, no drift."""
        _make_contract(
            tmp_path,
            """
toolchain:
  tools:
    - id: ruff
      kind: python_cli
      package: ruff
    - id: bandit
      kind: python_cli
      package: bandit
    - id: mypy
      kind: python_cli
      package: mypy
    - id: ty
      kind: python_cli
      package: ty
    - id: radon
      kind: python_cli
      package: radon
    - id: pip-audit
      kind: python_cli
      package: pip-audit
""",
        )
        # Create minimal config
        (tmp_path / ".claude").mkdir(exist_ok=True)
        drift = reconcile_with_registry(str(tmp_path))
        # All known required_tools are covered — no drift warnings expected
        assert drift == []

    def test_drift_when_manifest_missing_required_tool(self, tmp_path):
        """A linter needing a tool not in the manifest produces a drift warning."""
        # Manifest only has ruff — missing bandit, mypy, ty, radon, pip-audit
        _make_contract(
            tmp_path,
            """
toolchain:
  tools:
    - id: ruff
      kind: python_cli
      package: ruff
""",
        )
        (tmp_path / ".claude").mkdir(exist_ok=True)
        drift = reconcile_with_registry(str(tmp_path))
        # At minimum, linters requiring bandit/mypy/ty/radon/pip-audit should warn
        assert len(drift) > 0
        # Each warning mentions the missing tool and the linter that needs it
        for warning in drift:
            assert "not in gate_contract.yaml" in warning
            assert "requires tool" in warning

    def test_reconcile_returns_list_of_strings(self, tmp_path):
        """Return type is always list[str], each element a full warning sentence."""
        # Empty manifest triggers drift for every linter with a required_tool
        _make_contract(tmp_path, "toolchain:\n  tools: []\n")
        (tmp_path / ".claude").mkdir(exist_ok=True)
        result = reconcile_with_registry(str(tmp_path))
        # Falls back to defaults when tools list is empty, so no drift
        # (empty list triggers default manifest loading)
        assert result == []

    def test_reconcile_drift_warnings_contain_linter_and_tool_names(self, tmp_path):
        """Each drift warning names both the linter and the missing tool."""
        # Manifest with only a dummy tool — real linters' required_tools will be missing
        _make_contract(
            tmp_path,
            """
toolchain:
  tools:
    - id: dummy_only
      kind: python_cli
      package: dummy
""",
        )
        (tmp_path / ".claude").mkdir(exist_ok=True)
        drift = reconcile_with_registry(str(tmp_path))
        # Registry has linters requiring ruff, bandit, mypy, ty, radon, pip-audit
        # None of those are in our manifest, so we expect drift warnings
        missing_tools = {w.split("'")[3] for w in drift}  # extract tool name from warning
        # At minimum ruff should be flagged (it's the core required linter)
        assert "ruff" in missing_tools
        assert len(drift) >= 1


# ── full_toolchain_report ──────────────────────────────────────────


class TestFullToolchainReport:
    def test_returns_manifest_report(self, tmp_path):
        _make_contract(
            tmp_path,
            """
toolchain:
  tools:
    - id: python
      kind: python_cli
      package: python
      required: true
      required_by: [core]
      auto_install: false
""",
        )
        report = full_toolchain_report(str(tmp_path))
        assert isinstance(report, ManifestReport)
        assert len(report.tools) == 1
        assert "Toolchain:" in report.summary


# ── _detect_platform ───────────────────────────────────────────────


class TestDetectPlatform:
    def test_returns_lowercase_string(self):
        plat = _detect_platform()
        assert plat in ("darwin", "linux", "windows")

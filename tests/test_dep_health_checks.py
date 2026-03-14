"""Tests for lintgate/_dep_health_checks.py — dependency health check functions."""

from __future__ import annotations

import time
from unittest.mock import patch

from lintgate._dep_health_checks import (
    _check_conflicting_managers,
    _check_dep_churn,
    _check_lockfile_freshness,
    _check_lockfiles,
    _check_manifest_health,
    _check_python_version_file,
    _check_venv,
    _find_unpinned_deps,
    _report_found_lockfiles,
)

# ── _check_venv ────────────────────────────────────────────────────


class TestCheckVenv:
    def test_venv_with_python(self, tmp_path):
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").touch()
        (venv / "bin").mkdir()
        (venv / "bin" / "python").touch()
        (tmp_path / "pyproject.toml").touch()
        result = _check_venv(tmp_path)
        assert result.status == "ok"
        assert result.name == "virtual_environment"

    def test_venv_without_python(self, tmp_path):
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").touch()
        (venv / "bin").mkdir()
        (tmp_path / "pyproject.toml").touch()
        result = _check_venv(tmp_path)
        assert result.status == "warning"
        assert "no python executable" in result.message.lower()

    def test_no_venv_python_project(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()
        result = _check_venv(tmp_path)
        assert result.status == "error"

    def test_no_venv_non_python_project(self, tmp_path):
        result = _check_venv(tmp_path)
        assert result.status == "ok"
        assert "not needed" in result.message.lower()


# ── _check_lockfiles ──────────────────────────────────────────────


class TestCheckLockfiles:
    def test_no_manifests(self, tmp_path):
        checks = _check_lockfiles(tmp_path)
        assert len(checks) >= 1
        assert all(c.status == "ok" for c in checks)

    def test_manifest_without_lockfile(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        checks = _check_lockfiles(tmp_path)
        errors = [c for c in checks if c.status == "error"]
        assert len(errors) >= 1

    def test_manifest_with_lockfile(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / "uv.lock").touch()
        checks = _check_lockfiles(tmp_path)
        oks = [c for c in checks if c.status == "ok"]
        assert len(oks) >= 1


# ── _check_lockfile_freshness ─────────────────────────────────────


class TestCheckLockfileFreshness:
    def test_fresh_lockfile(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        lock = tmp_path / "uv.lock"
        lock.touch()
        # Ensure lock is newer by touching it after manifest
        import os

        os.utime(lock, (time.time() + 10, time.time() + 10))
        checks = _check_lockfile_freshness(tmp_path)
        oks = [c for c in checks if c.status == "ok"]
        assert len(oks) >= 1

    def test_stale_lockfile(self, tmp_path):
        lock = tmp_path / "uv.lock"
        lock.touch()
        manifest = tmp_path / "pyproject.toml"
        manifest.write_text('[project]\nname = "test"\n')
        # Make manifest newer
        import os

        os.utime(manifest, (time.time() + 100, time.time() + 100))
        checks = _check_lockfile_freshness(tmp_path)
        warnings = [c for c in checks if c.status == "warning"]
        assert len(warnings) >= 1

    def test_no_lockfiles(self, tmp_path):
        checks = _check_lockfile_freshness(tmp_path)
        assert checks == []


# ── _check_python_version_file ────────────────────────────────────


class TestCheckPythonVersionFile:
    def test_present(self, tmp_path):
        (tmp_path / ".python-version").write_text("3.11\n")
        result = _check_python_version_file(tmp_path)
        assert result.status == "ok"
        assert "3.11" in result.message

    def test_missing_no_python_project(self, tmp_path):
        result = _check_python_version_file(tmp_path)
        assert result.status == "ok"
        assert "not needed" in result.message.lower()

    def test_missing_python_project_no_ci(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()
        result = _check_python_version_file(tmp_path)
        assert result.status == "warning"

    def test_missing_python_project_with_ci(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()
        (tmp_path / ".github").mkdir()
        (tmp_path / ".github" / "workflows").mkdir()
        (tmp_path / ".github" / "workflows" / "ci.yml").touch()
        result = _check_python_version_file(tmp_path)
        assert result.status == "error"
        assert "ci" in result.message.lower()


# ── _check_conflicting_managers ───────────────────────────────────


class TestCheckConflictingManagers:
    def test_no_conflicts(self, tmp_path):
        checks = _check_conflicting_managers(tmp_path)
        assert checks == []

    def test_pipfile_and_poetry_lock(self, tmp_path):
        (tmp_path / "Pipfile").touch()
        (tmp_path / "poetry.lock").touch()
        checks = _check_conflicting_managers(tmp_path)
        assert len(checks) >= 1
        assert any(c.status in ("warning", "error") for c in checks)


# ── _find_unpinned_deps ───────────────────────────────────────────


class TestFindUnpinnedDeps:
    def test_pinned_deps(self):
        assert _find_unpinned_deps(["requests>=2.28", "click~=8.0"]) == []

    def test_unpinned_dep(self):
        result = _find_unpinned_deps(["requests", "click>=8.0"])
        assert result == ["requests"]

    def test_with_markers(self):
        result = _find_unpinned_deps(["foo; python_version >= '3.8'"])
        assert result == ["foo"]

    def test_pinned_with_markers(self):
        result = _find_unpinned_deps(["foo>=1.0; python_version >= '3.8'"])
        assert result == []

    def test_non_string_deps(self):
        result = _find_unpinned_deps([{"name": "foo"}, "bar>=1.0"])
        assert result == []

    def test_empty_deps(self):
        assert _find_unpinned_deps([]) == []

    def test_multiple_unpinned(self):
        result = _find_unpinned_deps(["foo", "bar", "baz>=1.0"])
        assert result == ["foo", "bar"]


# ── _check_dep_churn ──────────────────────────────────────────────


class TestCheckDepChurn:
    def test_no_history(self):
        with patch("lintgate._dep_health_checks._load_dep_history", return_value=None):
            result = _check_dep_churn("/fake")
        assert result.status == "ok"
        assert result.name == "dep_churn"
        assert result.message == "No dependency change history recorded"
        assert result.suggestion is None
        assert result.evidence == {}

    def test_low_churn(self):
        now = time.time()
        history = {"events": [{"timestamp": now - 1000}]}  # ~16min ago: outside 10min, inside 1hr
        with patch("lintgate._dep_health_checks._load_dep_history", return_value=history):
            result = _check_dep_churn("/fake")
        assert result.status == "ok"
        assert result.name == "dep_churn"
        assert result.evidence["last_10min"] == 0
        assert result.evidence["last_1hr"] == 1
        assert result.evidence["total_tracked"] == 1

    def test_high_churn(self):
        now = time.time()
        events = [{"timestamp": now - i} for i in range(5)]
        history = {"events": events}
        with patch("lintgate._dep_health_checks._load_dep_history", return_value=history):
            result = _check_dep_churn("/fake")
        assert result.status == "warning"
        assert result.name == "dep_churn"
        assert "5 dependency changes in last 10 minutes" in result.message
        assert result.suggestion == "Stabilize dependencies before continuing feature work"
        assert result.evidence["last_10min"] == 5
        assert result.evidence["last_1hr"] == 5
        assert result.evidence["total_tracked"] == 5


# ── Mutation-killing exact-value tests ───────────────────────────


class TestCheckVenvExact:
    """Exact field assertions to kill VALUE/BOUNDARY mutants."""

    def test_venv_ok_evidence(self, tmp_path):
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").touch()
        (venv / "bin").mkdir()
        (venv / "bin" / "python").touch()
        (tmp_path / "pyproject.toml").touch()
        result = _check_venv(tmp_path)
        assert result.name == "virtual_environment"
        assert result.status == "ok"
        assert result.message == "Virtual environment found at .venv/"
        assert result.evidence["path"] == str(venv)
        assert result.evidence["python"] == str(venv / "bin" / "python")
        assert result.suggestion is None

    def test_venv_no_python_exact(self, tmp_path):
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").touch()
        (venv / "bin").mkdir()
        (tmp_path / "pyproject.toml").touch()
        result = _check_venv(tmp_path)
        assert result.name == "virtual_environment"
        assert result.status == "warning"
        assert result.message == "Venv directory .venv/ exists but has no python executable"
        assert result.suggestion == "Recreate with `uv venv .venv`"
        assert result.evidence == {"path": str(venv)}

    def test_no_venv_error_exact(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()
        result = _check_venv(tmp_path)
        assert result.name == "virtual_environment"
        assert result.status == "error"
        assert result.message == "No virtual environment found in Python project"
        assert result.suggestion == "Run `uv venv .venv` then `uv pip install -e '.[dev]'`"

    def test_no_python_project_exact(self, tmp_path):
        result = _check_venv(tmp_path)
        assert result.name == "virtual_environment"
        assert result.status == "ok"
        assert result.message == "No Python project detected — venv not needed"
        assert result.suggestion is None
        assert result.evidence == {}


class TestReportFoundLockfiles:
    def test_with_uv_lock(self, tmp_path):
        (tmp_path / "uv.lock").touch()
        checks = _report_found_lockfiles(tmp_path)
        assert len(checks) == 1
        assert checks[0].name == "lockfile_uv.lock"
        assert checks[0].status == "ok"
        assert checks[0].message == "Lockfile uv.lock present"

    def test_no_lockfiles_no_manifests(self, tmp_path):
        checks = _report_found_lockfiles(tmp_path)
        assert len(checks) == 1
        assert checks[0].name == "lockfile"
        assert checks[0].status == "ok"
        assert checks[0].message == "No dependency manifests found — lockfile not needed"

    def test_multiple_lockfiles(self, tmp_path):
        (tmp_path / "uv.lock").touch()
        (tmp_path / "package-lock.json").touch()
        checks = _report_found_lockfiles(tmp_path)
        assert len(checks) == 2
        names = {c.name for c in checks}
        assert "lockfile_uv.lock" in names
        assert "lockfile_package-lock.json" in names


class TestCheckLockfilesExact:
    def test_missing_lockfile_exact(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        checks = _check_lockfiles(tmp_path)
        errors = [c for c in checks if c.status == "error"]
        assert len(errors) == 1
        assert errors[0].name == "lockfile_for_pyproject.toml"
        assert "pyproject.toml exists but no lockfile" in errors[0].message
        assert "uv.lock" in errors[0].message
        assert errors[0].suggestion == "Run `uv lock` to generate a lockfile"
        assert errors[0].evidence["manifest"] == "pyproject.toml"
        assert "uv.lock" in errors[0].evidence["expected_locks"]

    def test_lockfile_present_exact(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        (tmp_path / "uv.lock").touch()
        checks = _check_lockfiles(tmp_path)
        assert any(c.name == "lockfile_uv.lock" and c.status == "ok" for c in checks)


class TestCheckLockfileFreshnessExact:
    def test_fresh_exact(self, tmp_path):
        import os

        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        lock = tmp_path / "uv.lock"
        lock.touch()
        os.utime(lock, (time.time() + 10, time.time() + 10))
        checks = _check_lockfile_freshness(tmp_path)
        oks = [c for c in checks if c.status == "ok"]
        assert len(oks) == 1
        assert oks[0].name == "freshness_uv.lock"
        assert oks[0].message == "uv.lock is up to date with pyproject.toml"
        assert oks[0].suggestion is None

    def test_stale_exact(self, tmp_path):
        import os

        lock = tmp_path / "uv.lock"
        lock.touch()
        manifest = tmp_path / "pyproject.toml"
        manifest.write_text('[project]\nname = "test"\n')
        os.utime(manifest, (time.time() + 100, time.time() + 100))
        checks = _check_lockfile_freshness(tmp_path)
        warnings = [c for c in checks if c.status == "warning"]
        assert len(warnings) == 1
        assert warnings[0].name == "freshness_uv.lock"
        assert "uv.lock is" in warnings[0].message
        assert "older than pyproject.toml" in warnings[0].message
        assert warnings[0].suggestion == "Run `uv lock` to sync the lockfile"
        assert warnings[0].evidence["lock"] == "uv.lock"
        assert warnings[0].evidence["manifest"] == "pyproject.toml"
        assert warnings[0].evidence["staleness_seconds"] > 0


class TestCheckPythonVersionFileExact:
    def test_present_exact(self, tmp_path):
        (tmp_path / ".python-version").write_text("3.11\n")
        result = _check_python_version_file(tmp_path)
        assert result.name == "python_version_file"
        assert result.status == "ok"
        assert result.message == ".python-version specifies 3.11"
        assert result.evidence == {"version": "3.11"}
        assert result.suggestion is None

    def test_missing_no_python_exact(self, tmp_path):
        result = _check_python_version_file(tmp_path)
        assert result.name == "python_version_file"
        assert result.status == "ok"
        assert result.message == "No Python project — .python-version not needed"

    def test_missing_no_ci_exact(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()
        result = _check_python_version_file(tmp_path)
        assert result.name == "python_version_file"
        assert result.status == "warning"
        assert result.message == "No .python-version file — Python version not pinned"
        assert result.suggestion == "Create .python-version with your target version (e.g., '3.11')"
        assert result.evidence == {"has_ci": False}

    def test_missing_with_ci_exact(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()
        (tmp_path / ".github").mkdir()
        (tmp_path / ".github" / "workflows").mkdir()
        (tmp_path / ".github" / "workflows" / "ci.yml").touch()
        result = _check_python_version_file(tmp_path)
        assert result.name == "python_version_file"
        assert result.status == "error"
        assert (
            result.message
            == "No .python-version file — Python version not pinned (CI config detected — reproducibility is critical)"
        )
        assert result.suggestion == "Create .python-version with your target version (e.g., '3.11')"
        assert result.evidence == {"has_ci": True}


class TestCheckConflictingManagersExact:
    def test_pipfile_and_poetry_exact(self, tmp_path):
        (tmp_path / "Pipfile").touch()
        (tmp_path / "poetry.lock").touch()
        checks = _check_conflicting_managers(tmp_path)
        assert len(checks) == 1
        c = checks[0]
        assert c.name == "conflict_Pipfile_poetry.lock"
        assert (
            c.status == "warning"
        )  # Pipfile is manifest, poetry.lock is lockfile — not both lockfiles
        assert c.message == "Both Pipfile (pipenv) and poetry.lock exist — pick one"
        assert (
            c.suggestion
            == "Remove one and consolidate on a single package manager (uv recommended)"
        )
        assert c.evidence["file_a"] == "Pipfile"
        assert c.evidence["file_b"] == "poetry.lock"
        assert c.evidence["both_lockfiles"] is False

    def test_both_lockfiles_escalates_to_error(self, tmp_path):
        (tmp_path / "poetry.lock").touch()
        (tmp_path / "uv.lock").touch()
        checks = _check_conflicting_managers(tmp_path)
        assert len(checks) == 1
        c = checks[0]
        assert c.name == "conflict_poetry.lock_uv.lock"
        assert c.status == "error"  # Both are lockfiles — escalated
        assert c.evidence["both_lockfiles"] is True


class TestCheckManifestHealth:
    def test_no_pyproject(self, tmp_path):
        checks = _check_manifest_health(tmp_path)
        assert checks == []

    def test_requires_python_present(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "test"\nrequires-python = ">=3.10"\n'
            '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
        )
        checks = _check_manifest_health(tmp_path)
        rp = [c for c in checks if c.name == "manifest_requires_python"]
        assert len(rp) == 1
        assert rp[0].status == "ok"
        assert rp[0].message == "requires-python = '>=3.10'"

    def test_requires_python_missing(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "test"\n'
            '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
        )
        checks = _check_manifest_health(tmp_path)
        rp = [c for c in checks if c.name == "manifest_requires_python"]
        assert len(rp) == 1
        assert rp[0].status == "warning"
        assert rp[0].message == "pyproject.toml missing requires-python field"
        assert rp[0].suggestion == "Add requires-python = '>=3.10' (or your minimum version)"
        assert rp[0].evidence == {"manifest": "pyproject.toml", "issue": "missing_requires_python"}

    def test_build_system_missing(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        checks = _check_manifest_health(tmp_path)
        bs = [c for c in checks if c.name == "manifest_build_system"]
        assert len(bs) == 1
        assert bs[0].status == "warning"
        assert bs[0].message == "pyproject.toml missing [build-system] — cannot install as package"
        assert bs[0].suggestion == "Add [build-system] with hatchling, setuptools, or flit"

    def test_unpinned_deps(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "test"\ndependencies = ["requests", "click>=8.0"]\n'
            '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
        )
        checks = _check_manifest_health(tmp_path)
        up = [c for c in checks if c.name == "manifest_unpinned_deps"]
        assert len(up) == 1
        assert up[0].status == "warning"
        assert "requests" in up[0].message
        assert "click" not in up[0].message  # click is pinned
        assert up[0].suggestion == "Add minimum version constraints (e.g., 'requests>=2.28')"
        assert up[0].evidence["unpinned"] == ["requests"]
        assert up[0].evidence["manifest"] == "pyproject.toml"
        assert up[0].evidence["issue"] == "unpinned_core_deps"

    def test_parse_error(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("not valid toml {{{}}")
        checks = _check_manifest_health(tmp_path)
        assert len(checks) == 1
        assert checks[0].name == "manifest_parse"
        assert checks[0].status == "error"
        assert checks[0].message == "pyproject.toml failed to parse"

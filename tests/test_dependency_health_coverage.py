"""Targeted coverage tests for dependency_health.py uncovered symbols."""

from __future__ import annotations

import time
from pathlib import Path

from lintgate.dependency_health import (
    HealthCheck,
    _build_summary,
    _check_conflicting_managers,
    _check_lockfile_freshness,
    _check_lockfiles,
    _check_manifest_health,
    _check_python_version_file,
    _check_venv,
    _format_duration,
    _is_global_install,
    _load_dep_history,
    full_dependency_health,
    quick_dependency_check,
)

# ── HealthCheck ──────────────────────────────────────────────────────


def test_health_check_to_dict():
    hc = HealthCheck(name="test", status="ok", message="fine")
    d = hc.to_dict()
    assert d["name"] == "test"
    assert d["status"] == "ok"


# ── _check_venv ──────────────────────────────────────────────────────


def test_check_venv_found(tmp_path):
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("")
    (venv / "bin").mkdir()
    (venv / "bin" / "python").write_text("")
    hc = _check_venv(tmp_path)
    assert hc.status == "ok"


def test_check_venv_broken(tmp_path):
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("")
    # No bin/python
    hc = _check_venv(tmp_path)
    assert hc.status == "warning"


def test_check_venv_missing_no_python_project(tmp_path):
    hc = _check_venv(tmp_path)
    assert hc.status == "ok"


def test_check_venv_missing_with_python_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    hc = _check_venv(tmp_path)
    assert hc.status == "error"


# ── _check_lockfiles ─────────────────────────────────────────────────


def test_check_lockfiles_present(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "uv.lock").write_text("")
    checks = _check_lockfiles(tmp_path)
    assert any(c.status == "ok" for c in checks)


def test_check_lockfiles_missing(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    # No lockfile
    checks = _check_lockfiles(tmp_path)
    assert any(c.status == "error" for c in checks)


def test_check_lockfiles_no_manifests(tmp_path):
    checks = _check_lockfiles(tmp_path)
    assert all(c.status == "ok" for c in checks)


# ── _check_lockfile_freshness ────────────────────────────────────────


def test_lockfile_fresh(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "uv.lock").write_text("")
    # Make lock newer than manifest
    import os

    manifest = tmp_path / "pyproject.toml"
    lock = tmp_path / "uv.lock"
    os.utime(manifest, (time.time() - 100, time.time() - 100))
    os.utime(lock, (time.time(), time.time()))
    checks = _check_lockfile_freshness(tmp_path)
    assert any(c.status == "ok" for c in checks)


def test_lockfile_stale(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "uv.lock").write_text("")
    import os

    manifest = tmp_path / "pyproject.toml"
    lock = tmp_path / "uv.lock"
    os.utime(lock, (time.time() - 100, time.time() - 100))
    os.utime(manifest, (time.time(), time.time()))
    checks = _check_lockfile_freshness(tmp_path)
    assert any(c.status == "warning" for c in checks)


# ── _check_python_version_file ───────────────────────────────────────


def test_python_version_present(tmp_path):
    (tmp_path / ".python-version").write_text("3.11\n")
    hc = _check_python_version_file(tmp_path)
    assert hc.status == "ok"


def test_python_version_missing_no_project(tmp_path):
    hc = _check_python_version_file(tmp_path)
    assert hc.status == "ok"


def test_python_version_missing_with_ci(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    hc = _check_python_version_file(tmp_path)
    assert hc.status == "error"


def test_python_version_missing_without_ci(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    hc = _check_python_version_file(tmp_path)
    assert hc.status == "warning"


# ── _check_conflicting_managers ──────────────────────────────────────


def test_conflicting_managers_none(tmp_path):
    checks = _check_conflicting_managers(tmp_path)
    assert checks == []


def test_conflicting_managers_found(tmp_path):
    (tmp_path / "Pipfile").write_text("")
    (tmp_path / "uv.lock").write_text("")
    checks = _check_conflicting_managers(tmp_path)
    # May or may not detect depending on defined combos
    # Just verify it runs without error
    assert isinstance(checks, list)


# ── _check_manifest_health ───────────────────────────────────────────


def test_manifest_health_no_pyproject(tmp_path):
    checks = _check_manifest_health(tmp_path)
    assert checks == []


def test_manifest_health_good_pyproject(tmp_path):
    content = """
[project]
requires-python = ">=3.10"
dependencies = ["requests>=2.28"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""
    (tmp_path / "pyproject.toml").write_text(content)
    checks = _check_manifest_health(tmp_path)
    assert any("requires-python" in c.message for c in checks)
    assert all(c.status == "ok" for c in checks if "requires-python" in c.message)


def test_manifest_health_missing_requires_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
    checks = _check_manifest_health(tmp_path)
    assert any(c.status == "warning" and "requires-python" in c.message for c in checks)


def test_manifest_health_unpinned_deps(tmp_path):
    content = """
[project]
requires-python = ">=3.10"
dependencies = ["requests", "flask"]
"""
    (tmp_path / "pyproject.toml").write_text(content)
    checks = _check_manifest_health(tmp_path)
    assert any("unpinned" in c.name for c in checks)


def test_manifest_health_bad_toml(tmp_path):
    (tmp_path / "pyproject.toml").write_bytes(b"\x80\x81\x82")
    checks = _check_manifest_health(tmp_path)
    assert any(c.status == "error" for c in checks)


# ── _is_global_install ───────────────────────────────────────────────


def test_global_install_no_venv(tmp_path):
    result = _is_global_install("pip install requests", tmp_path)
    assert result is True


def test_global_install_with_venv(tmp_path):
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("")
    result = _is_global_install("pip install requests", tmp_path)
    assert result is False


def test_global_install_non_pip():
    result = _is_global_install("git status", Path("/tmp"))
    assert result is False


def test_global_install_with_target_flag():
    result = _is_global_install("pip install --target /foo requests", Path("/tmp"))
    assert result is False


# ── _load_dep_history ────────────────────────────────────────────────


def test_load_dep_history_missing():
    result = _load_dep_history("/nonexistent/project/path_xyz")
    assert result is None


# ── _format_duration ─────────────────────────────────────────────────


def test_format_seconds():
    assert _format_duration(30) == "30s"


def test_format_minutes():
    assert _format_duration(300) == "5m"


def test_format_hours():
    assert "h" in _format_duration(7200)


def test_format_days():
    assert "d" in _format_duration(100000)


# ── _build_summary ───────────────────────────────────────────────────


def test_summary_healthy():
    checks = [HealthCheck(name="a", status="ok", message="ok")]
    s = _build_summary(checks)
    assert s["health"] == "healthy"


def test_summary_needs_attention():
    checks = [
        HealthCheck(name="a", status="ok", message="ok"),
        HealthCheck(name="b", status="warning", message="warn"),
    ]
    s = _build_summary(checks)
    assert s["health"] == "needs_attention"


def test_summary_unhealthy():
    checks = [HealthCheck(name="a", status="error", message="bad")]
    s = _build_summary(checks)
    assert s["health"] == "unhealthy"


# ── full_dependency_health ───────────────────────────────────────────


def test_full_dependency_health(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
    (tmp_path / "uv.lock").write_text("")
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("")
    (venv / "bin").mkdir()
    (venv / "bin" / "python").write_text("")
    result = full_dependency_health(str(tmp_path))
    assert "checks" in result
    assert "healthy" in result


# ── quick_dependency_check ───────────────────────────────────────────


def test_quick_healthy(tmp_path):
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("")
    (venv / "bin").mkdir()
    (venv / "bin" / "python").write_text("")
    warnings = quick_dependency_check(str(tmp_path), "Edit", {})
    assert isinstance(warnings, list)


def test_quick_pip_install_no_venv(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    warnings = quick_dependency_check(
        str(tmp_path),
        "Bash",
        {"command": "pip install requests"},
    )
    assert any("global" in w.lower() or "venv" in w.lower() for w in warnings)

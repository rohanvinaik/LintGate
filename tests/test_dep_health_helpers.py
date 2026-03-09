"""Tests for lintgate/_dep_health_helpers.py — sub-module level coverage."""

from __future__ import annotations

import time
from unittest.mock import patch

from lintgate._dep_health_helpers import (
    _LOCK_TO_MANIFEST,
    _LOCKFILES,
    _MANIFEST_TO_LOCK,
    _MANIFESTS,
    _VENV_DIRS,
    HealthCheck,
    _find_venv,
    _format_duration,
    _has_ci_config,
    _has_python_project,
    _is_global_install,
    _load_json,
    _missing_lockfiles,
    _record_dep_event,
    _stale_lockfiles,
)

# ── HealthCheck.to_dict ────────────────────────────────────────────


def test_to_dict_minimal():
    hc = HealthCheck(name="check1", status="ok", message="all good")
    d = hc.to_dict()
    assert d == {"name": "check1", "status": "ok", "message": "all good"}
    assert "suggestion" not in d
    assert "evidence" not in d


def test_to_dict_with_suggestion():
    hc = HealthCheck(name="check2", status="warning", message="stale", suggestion="run uv lock")
    d = hc.to_dict()
    assert d["suggestion"] == "run uv lock"
    assert "evidence" not in d


def test_to_dict_with_evidence():
    hc = HealthCheck(
        name="check3", status="error", message="bad", evidence={"lockfile": "uv.lock"}
    )
    d = hc.to_dict()
    assert d["evidence"] == {"lockfile": "uv.lock"}
    assert "suggestion" not in d


def test_to_dict_with_both():
    hc = HealthCheck(
        name="check4",
        status="warning",
        message="issue",
        suggestion="fix it",
        evidence={"a": 1},
    )
    d = hc.to_dict()
    assert d["suggestion"] == "fix it"
    assert d["evidence"] == {"a": 1}


def test_to_dict_empty_suggestion_excluded():
    hc = HealthCheck(name="x", status="ok", message="m", suggestion="")
    d = hc.to_dict()
    assert "suggestion" not in d


def test_to_dict_empty_evidence_excluded():
    hc = HealthCheck(name="x", status="ok", message="m", evidence={})
    d = hc.to_dict()
    assert "evidence" not in d


# ── _format_duration ───────────────────────────────────────────────


def test_format_duration_zero():
    assert _format_duration(0) == "0s"


def test_format_duration_seconds():
    assert _format_duration(30) == "30s"


def test_format_duration_59_seconds():
    assert _format_duration(59) == "59s"


def test_format_duration_60_seconds_boundary():
    assert _format_duration(60) == "1m"


def test_format_duration_minutes():
    assert _format_duration(120) == "2m"


def test_format_duration_3599_seconds():
    assert _format_duration(3599) == "59m"


def test_format_duration_3600_seconds_boundary():
    assert _format_duration(3600) == "1.0h"


def test_format_duration_hours():
    assert _format_duration(7200) == "2.0h"


def test_format_duration_86399_seconds():
    result = _format_duration(86399)
    assert result == "24.0h"


def test_format_duration_86400_boundary():
    assert _format_duration(86400) == "1.0d"


def test_format_duration_days():
    assert _format_duration(172800) == "2.0d"


def test_format_duration_fractional_hours():
    assert _format_duration(5400) == "1.5h"


# ── Constants ──────────────────────────────────────────────────────


def test_lockfiles_contains_python():
    assert "python" in _LOCKFILES
    assert "uv.lock" in _LOCKFILES["python"]
    assert "poetry.lock" in _LOCKFILES["python"]


def test_lockfiles_contains_node():
    assert "node" in _LOCKFILES
    assert "package-lock.json" in _LOCKFILES["node"]


def test_lockfiles_contains_rust():
    assert "rust" in _LOCKFILES
    assert "Cargo.lock" in _LOCKFILES["rust"]


def test_lockfiles_contains_go():
    assert "go" in _LOCKFILES
    assert "go.sum" in _LOCKFILES["go"]


def test_manifests_python():
    assert "pyproject.toml" in _MANIFESTS["python"]
    assert "setup.py" in _MANIFESTS["python"]


def test_manifests_node():
    assert "package.json" in _MANIFESTS["node"]


def test_lock_to_manifest_uv():
    assert _LOCK_TO_MANIFEST["uv.lock"] == "pyproject.toml"


def test_lock_to_manifest_poetry():
    assert _LOCK_TO_MANIFEST["poetry.lock"] == "pyproject.toml"


def test_lock_to_manifest_cargo():
    assert _LOCK_TO_MANIFEST["Cargo.lock"] == "Cargo.toml"


def test_lock_to_manifest_go():
    assert _LOCK_TO_MANIFEST["go.sum"] == "go.mod"


def test_manifest_to_lock_pyproject():
    assert "uv.lock" in _MANIFEST_TO_LOCK["pyproject.toml"]
    assert "poetry.lock" in _MANIFEST_TO_LOCK["pyproject.toml"]


# ── _find_venv ─────────────────────────────────────────────────────


def test_find_venv_found(tmp_path):
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = /usr/bin")
    result = _find_venv(tmp_path)
    assert result == venv


def test_find_venv_not_found(tmp_path):
    result = _find_venv(tmp_path)
    assert result is None


def test_find_venv_dir_without_cfg(tmp_path):
    (tmp_path / ".venv").mkdir()
    result = _find_venv(tmp_path)
    assert result is None


def test_find_venv_alternative_names(tmp_path):
    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("")
    result = _find_venv(tmp_path)
    assert result == venv


# ── _has_python_project ────────────────────────────────────────────


def test_has_python_project_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    assert _has_python_project(tmp_path) is True


def test_has_python_project_setup_py(tmp_path):
    (tmp_path / "setup.py").write_text("")
    assert _has_python_project(tmp_path) is True


def test_has_python_project_none(tmp_path):
    assert _has_python_project(tmp_path) is False


# ── _has_ci_config ─────────────────────────────────────────────────


def test_has_ci_config_github(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    assert _has_ci_config(tmp_path) is True


def test_has_ci_config_gitlab(tmp_path):
    (tmp_path / ".gitlab-ci.yml").write_text("")
    assert _has_ci_config(tmp_path) is True


def test_has_ci_config_none(tmp_path):
    assert _has_ci_config(tmp_path) is False


# ── _missing_lockfiles ─────────────────────────────────────────────


def test_missing_lockfiles_pyproject_no_lock(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    result = _missing_lockfiles(tmp_path)
    assert len(result) == 1
    assert result[0][0] == "pyproject.toml"


def test_missing_lockfiles_pyproject_with_uv_lock(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "uv.lock").write_text("")
    result = _missing_lockfiles(tmp_path)
    assert len(result) == 0


def test_missing_lockfiles_empty_project(tmp_path):
    result = _missing_lockfiles(tmp_path)
    assert result == []


# ── _stale_lockfiles ───────────────────────────────────────────────


def test_stale_lockfiles_stale(tmp_path):
    lock = tmp_path / "uv.lock"
    manifest = tmp_path / "pyproject.toml"
    lock.write_text("")
    time.sleep(0.05)
    manifest.write_text("")
    result = _stale_lockfiles(tmp_path)
    assert len(result) == 1
    assert result[0] == ("pyproject.toml", "uv.lock")


def test_stale_lockfiles_fresh(tmp_path):
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text("")
    time.sleep(0.05)
    lock = tmp_path / "uv.lock"
    lock.write_text("")
    result = _stale_lockfiles(tmp_path)
    assert len(result) == 0


def test_stale_lockfiles_no_files(tmp_path):
    result = _stale_lockfiles(tmp_path)
    assert result == []


# ── _is_global_install ─────────────────────────────────────────────


def test_is_global_install_pip_no_venv(tmp_path):
    assert _is_global_install("pip install requests", tmp_path) is True


def test_is_global_install_pip3_no_venv(tmp_path):
    assert _is_global_install("pip3 install requests", tmp_path) is True


def test_is_global_install_uv_pip_no_venv(tmp_path):
    assert _is_global_install("uv pip install requests", tmp_path) is True


def test_is_global_install_uv_install_no_venv(tmp_path):
    assert _is_global_install("uv install requests", tmp_path) is True


def test_is_global_install_with_venv(tmp_path):
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("")
    assert _is_global_install("pip install requests", tmp_path) is False


def test_is_global_install_with_target_flag(tmp_path):
    assert _is_global_install("pip install --target /foo requests", tmp_path) is False


def test_is_global_install_non_install_command(tmp_path):
    assert _is_global_install("git commit -m 'msg'", tmp_path) is False


# ── _load_json ─────────────────────────────────────────────────────


def test_load_json_valid(tmp_path):
    p = tmp_path / "data.json"
    p.write_text('{"key": "value"}')
    result = _load_json(p)
    assert result == {"key": "value"}


def test_load_json_nonexistent(tmp_path):
    result = _load_json(tmp_path / "nope.json")
    assert result is None


def test_load_json_invalid(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json")
    result = _load_json(p)
    assert result is None


def test_load_json_non_dict(tmp_path):
    p = tmp_path / "list.json"
    p.write_text("[1, 2, 3]")
    result = _load_json(p)
    assert result is None


# ── _record_dep_event ──────────────────────────────────────────────


def test_record_dep_event_creates_history(tmp_path):
    with patch("lintgate._dep_health_helpers.DEP_HEALTH_DIR", tmp_path):
        result = _record_dep_event("/test/project", "install")
    assert result is not None
    assert "recent_count" in result
    assert result["recent_count"] >= 1
    assert result["is_churning"] is False


def test_record_dep_event_churn_detection(tmp_path):
    with patch("lintgate._dep_health_helpers.DEP_HEALTH_DIR", tmp_path):
        for _ in range(5):
            result = _record_dep_event("/test/project", "install")
    assert result is not None
    assert result["is_churning"] is True
    assert result["recent_count"] >= 5


# ── _VENV_DIRS constant ───────────────────────────────────────────


def test_venv_dirs_values():
    assert ".venv" in _VENV_DIRS
    assert "venv" in _VENV_DIRS
    assert "env" in _VENV_DIRS
    assert ".env" in _VENV_DIRS

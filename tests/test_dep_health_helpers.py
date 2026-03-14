"""Tests for lintgate/_dep_health_helpers.py — sub-module level coverage."""

from __future__ import annotations

import json
import os
import time
from unittest.mock import patch

from lintgate._dep_health_helpers import (
    _CONFLICTING_COMBOS,
    _LOCK_TO_MANIFEST,
    _LOCKFILES,
    _MANIFEST_TO_LOCK,
    _MANIFESTS,
    _VENV_DIRS,
    _VENV_INDICATOR_FLAGS,
    HealthCheck,
    _find_venv,
    _format_duration,
    _has_ci_config,
    _has_python_project,
    _is_global_install,
    _load_dep_history,
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


def test_to_dict_minimal_exact_keys():
    """Mutant killer: verify exactly 3 keys when no optional fields."""
    hc = HealthCheck(name="n", status="ok", message="m")
    d = hc.to_dict()
    assert set(d.keys()) == {"name", "status", "message"}
    assert len(d) == 3


def test_to_dict_with_suggestion():
    hc = HealthCheck(name="check2", status="warning", message="stale", suggestion="run uv lock")
    d = hc.to_dict()
    assert d["suggestion"] == "run uv lock"
    assert "evidence" not in d


def test_to_dict_with_suggestion_exact_keys():
    """Mutant killer: exactly 4 keys when suggestion present, no evidence."""
    hc = HealthCheck(name="n", status="ok", message="m", suggestion="s")
    d = hc.to_dict()
    assert set(d.keys()) == {"name", "status", "message", "suggestion"}
    assert len(d) == 4


def test_to_dict_with_evidence():
    hc = HealthCheck(name="check3", status="error", message="bad", evidence={"lockfile": "uv.lock"})
    d = hc.to_dict()
    assert d["evidence"] == {"lockfile": "uv.lock"}
    assert "suggestion" not in d


def test_to_dict_with_evidence_exact_keys():
    """Mutant killer: exactly 4 keys when evidence present, no suggestion."""
    hc = HealthCheck(name="n", status="ok", message="m", evidence={"k": 1})
    d = hc.to_dict()
    assert set(d.keys()) == {"name", "status", "message", "evidence"}
    assert len(d) == 4


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


def test_to_dict_with_both_exact_keys():
    """Mutant killer: exactly 5 keys when both optional fields present."""
    hc = HealthCheck(name="n", status="w", message="m", suggestion="s", evidence={"k": 1})
    d = hc.to_dict()
    assert set(d.keys()) == {"name", "status", "message", "suggestion", "evidence"}
    assert len(d) == 5


def test_to_dict_empty_suggestion_excluded():
    hc = HealthCheck(name="x", status="ok", message="m", suggestion="")
    d = hc.to_dict()
    assert "suggestion" not in d


def test_to_dict_empty_evidence_excluded():
    hc = HealthCheck(name="x", status="ok", message="m", evidence={})
    d = hc.to_dict()
    assert "evidence" not in d


def test_to_dict_none_suggestion_excluded():
    """Mutant killer: suggestion=None must be excluded (default)."""
    hc = HealthCheck(name="x", status="ok", message="m", suggestion=None)
    d = hc.to_dict()
    assert "suggestion" not in d
    assert len(d) == 3


def test_to_dict_preserves_exact_field_values():
    """Mutant killer: verify each field maps to the exact input value."""
    hc = HealthCheck(name="alpha", status="error", message="beta")
    d = hc.to_dict()
    assert d["name"] == "alpha"
    assert d["status"] == "error"
    assert d["message"] == "beta"


def test_to_dict_evidence_nested_structure():
    """Mutant killer: verify nested evidence dict is preserved, not flattened."""
    ev = {"files": ["a.py", "b.py"], "count": 2}
    hc = HealthCheck(name="n", status="ok", message="m", evidence=ev)
    d = hc.to_dict()
    assert d["evidence"]["files"] == ["a.py", "b.py"]
    assert d["evidence"]["count"] == 2


def test_to_dict_returns_new_dict():
    """Mutant killer: to_dict() must return a new dict each call."""
    hc = HealthCheck(name="n", status="ok", message="m")
    d1 = hc.to_dict()
    d2 = hc.to_dict()
    assert d1 == d2
    assert d1 is not d2


# ── HealthCheck dataclass defaults ───────────────────────────────


def test_healthcheck_default_suggestion_is_none():
    hc = HealthCheck(name="n", status="ok", message="m")
    assert hc.suggestion is None


def test_healthcheck_default_evidence_is_empty_dict():
    hc = HealthCheck(name="n", status="ok", message="m")
    assert hc.evidence == {}
    assert isinstance(hc.evidence, dict)


def test_healthcheck_evidence_default_factory_independence():
    """Mutant killer: default_factory must create independent dicts."""
    hc1 = HealthCheck(name="n1", status="ok", message="m1")
    hc2 = HealthCheck(name="n2", status="ok", message="m2")
    hc1.evidence["key"] = "val"
    assert "key" not in hc2.evidence


# ── _format_duration ───────────────────────────────────────────────


def test_format_duration_zero():
    assert _format_duration(0) == "0s"


def test_format_duration_one_second():
    """Mutant killer: verify 1 second formats correctly."""
    assert _format_duration(1) == "1s"


def test_format_duration_seconds():
    assert _format_duration(30) == "30s"


def test_format_duration_59_seconds():
    assert _format_duration(59) == "59s"


def test_format_duration_59_point_9_truncates():
    """Mutant killer: fractional seconds are truncated (int()), not rounded."""
    assert _format_duration(59.9) == "59s"


def test_format_duration_60_seconds_boundary():
    assert _format_duration(60) == "1m"


def test_format_duration_minutes():
    assert _format_duration(120) == "2m"


def test_format_duration_90_seconds_is_1m():
    """Mutant killer: 90s -> 1m (int(90/60) = 1, not rounded up to 2)."""
    assert _format_duration(90) == "1m"


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


def test_format_duration_fractional_days():
    """Mutant killer: verify fractional day formatting."""
    assert _format_duration(129600) == "1.5d"


def test_format_duration_format_suffixes():
    """Mutant killer: verify the suffix characters are exactly s, m, h, d."""
    assert _format_duration(10).endswith("s")
    assert _format_duration(600).endswith("m")
    assert _format_duration(7200).endswith("h")
    assert _format_duration(172800).endswith("d")


def test_format_duration_boundary_below_60():
    """Mutant killer: 59.999 is still in seconds range (< 60)."""
    result = _format_duration(59.999)
    assert result == "59s"


def test_format_duration_minutes_int_truncation():
    """Mutant killer: 119s -> 1m (int(119/60)=1), not 2."""
    assert _format_duration(119) == "1m"


def test_format_duration_hours_decimal_format():
    """Mutant killer: hours use .1f format — 3661s -> 1.0h not 1h."""
    result = _format_duration(3661)
    assert result == "1.0h"


def test_format_duration_days_decimal_format():
    """Mutant killer: days use .1f format — 86401 -> 1.0d not 1d."""
    assert _format_duration(86401) == "1.0d"


# ── Constants ──────────────────────────────────────────────────────


def test_lockfiles_contains_python():
    assert "python" in _LOCKFILES
    assert "uv.lock" in _LOCKFILES["python"]
    assert "poetry.lock" in _LOCKFILES["python"]


def test_lockfiles_python_complete():
    """Mutant killer: exact list of Python lockfiles."""
    assert _LOCKFILES["python"] == ["uv.lock", "poetry.lock", "Pipfile.lock", "requirements.txt"]


def test_lockfiles_contains_node():
    assert "node" in _LOCKFILES
    assert "package-lock.json" in _LOCKFILES["node"]


def test_lockfiles_node_complete():
    """Mutant killer: exact list of Node lockfiles."""
    assert _LOCKFILES["node"] == ["package-lock.json", "yarn.lock", "pnpm-lock.yaml"]


def test_lockfiles_contains_rust():
    assert "rust" in _LOCKFILES
    assert "Cargo.lock" in _LOCKFILES["rust"]


def test_lockfiles_contains_go():
    assert "go" in _LOCKFILES
    assert "go.sum" in _LOCKFILES["go"]


def test_lockfiles_exactly_four_ecosystems():
    """Mutant killer: exactly 4 ecosystem keys."""
    assert set(_LOCKFILES.keys()) == {"python", "node", "rust", "go"}


def test_manifests_python():
    assert "pyproject.toml" in _MANIFESTS["python"]
    assert "setup.py" in _MANIFESTS["python"]


def test_manifests_python_complete():
    """Mutant killer: exact list of Python manifests."""
    assert _MANIFESTS["python"] == [
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
        "Pipfile",
        "requirements.in",
    ]


def test_manifests_node():
    assert "package.json" in _MANIFESTS["node"]


def test_manifests_rust():
    """Mutant killer: Rust manifest."""
    assert _MANIFESTS["rust"] == ["Cargo.toml"]


def test_manifests_go():
    """Mutant killer: Go manifest."""
    assert _MANIFESTS["go"] == ["go.mod"]


def test_lock_to_manifest_uv():
    assert _LOCK_TO_MANIFEST["uv.lock"] == "pyproject.toml"


def test_lock_to_manifest_poetry():
    assert _LOCK_TO_MANIFEST["poetry.lock"] == "pyproject.toml"


def test_lock_to_manifest_pipfile():
    """Mutant killer: Pipfile.lock -> Pipfile."""
    assert _LOCK_TO_MANIFEST["Pipfile.lock"] == "Pipfile"


def test_lock_to_manifest_npm():
    """Mutant killer: package-lock.json -> package.json."""
    assert _LOCK_TO_MANIFEST["package-lock.json"] == "package.json"


def test_lock_to_manifest_yarn():
    """Mutant killer: yarn.lock -> package.json."""
    assert _LOCK_TO_MANIFEST["yarn.lock"] == "package.json"


def test_lock_to_manifest_pnpm():
    """Mutant killer: pnpm-lock.yaml -> package.json."""
    assert _LOCK_TO_MANIFEST["pnpm-lock.yaml"] == "package.json"


def test_lock_to_manifest_cargo():
    assert _LOCK_TO_MANIFEST["Cargo.lock"] == "Cargo.toml"


def test_lock_to_manifest_go():
    assert _LOCK_TO_MANIFEST["go.sum"] == "go.mod"


def test_lock_to_manifest_exact_count():
    """Mutant killer: exactly 8 lock-to-manifest entries."""
    assert len(_LOCK_TO_MANIFEST) == 8


def test_manifest_to_lock_pyproject():
    assert "uv.lock" in _MANIFEST_TO_LOCK["pyproject.toml"]
    assert "poetry.lock" in _MANIFEST_TO_LOCK["pyproject.toml"]


def test_manifest_to_lock_pipfile():
    """Mutant killer: Pipfile -> [Pipfile.lock]."""
    assert _MANIFEST_TO_LOCK["Pipfile"] == ["Pipfile.lock"]


def test_manifest_to_lock_requirements_in():
    """Mutant killer: requirements.in -> [requirements.txt]."""
    assert _MANIFEST_TO_LOCK["requirements.in"] == ["requirements.txt"]


def test_manifest_to_lock_package_json():
    """Mutant killer: package.json maps to all 3 Node lockfiles."""
    expected = ["package-lock.json", "yarn.lock", "pnpm-lock.yaml"]
    assert _MANIFEST_TO_LOCK["package.json"] == expected


def test_manifest_to_lock_cargo():
    """Mutant killer: Cargo.toml -> [Cargo.lock]."""
    assert _MANIFEST_TO_LOCK["Cargo.toml"] == ["Cargo.lock"]


def test_manifest_to_lock_go():
    """Mutant killer: go.mod -> [go.sum]."""
    assert _MANIFEST_TO_LOCK["go.mod"] == ["go.sum"]


def test_manifest_to_lock_exact_count():
    """Mutant killer: exactly 6 manifest-to-lock entries."""
    assert len(_MANIFEST_TO_LOCK) == 6


# ── _CONFLICTING_COMBOS ──────────────────────────────────────────


def test_conflicting_combos_count():
    """Mutant killer: exactly 3 conflicting combos."""
    assert len(_CONFLICTING_COMBOS) == 3


def test_conflicting_combos_pipfile_poetry():
    """Mutant killer: first combo is Pipfile vs poetry.lock."""
    combo = _CONFLICTING_COMBOS[0]
    assert combo[0] == "Pipfile"
    assert combo[1] == "poetry.lock"
    assert "pipenv" in combo[2].lower() or "Pipfile" in combo[2]


def test_conflicting_combos_pipfile_uv():
    """Mutant killer: second combo is Pipfile vs uv.lock."""
    combo = _CONFLICTING_COMBOS[1]
    assert combo[0] == "Pipfile"
    assert combo[1] == "uv.lock"


def test_conflicting_combos_poetry_uv():
    """Mutant killer: third combo is poetry.lock vs uv.lock."""
    combo = _CONFLICTING_COMBOS[2]
    assert combo[0] == "poetry.lock"
    assert combo[1] == "uv.lock"


def test_conflicting_combos_all_are_three_tuples():
    """Mutant killer: each combo has exactly 3 elements (file1, file2, message)."""
    for combo in _CONFLICTING_COMBOS:
        assert len(combo) == 3
        assert isinstance(combo[0], str)
        assert isinstance(combo[1], str)
        assert isinstance(combo[2], str)


# ── _VENV_INDICATOR_FLAGS ────────────────────────────────────────


def test_venv_indicator_flags_content():
    """Mutant killer: exact set of flags that prevent global install detection."""
    assert "--target" in _VENV_INDICATOR_FLAGS
    assert "-t " in _VENV_INDICATOR_FLAGS
    assert "--prefix" in _VENV_INDICATOR_FLAGS
    assert len(_VENV_INDICATOR_FLAGS) == 3


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


def test_find_venv_env_name(tmp_path):
    """Mutant killer: 'env' directory is also detected."""
    venv = tmp_path / "env"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("")
    result = _find_venv(tmp_path)
    assert result == venv


def test_find_venv_dot_env_name(tmp_path):
    """Mutant killer: '.env' directory is also detected."""
    venv = tmp_path / ".env"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("")
    result = _find_venv(tmp_path)
    assert result == venv


def test_find_venv_priority_returns_first_match(tmp_path):
    """Mutant killer: .venv is preferred over venv (iteration order)."""
    for name in (".venv", "venv"):
        d = tmp_path / name
        d.mkdir()
        (d / "pyvenv.cfg").write_text("")
    result = _find_venv(tmp_path)
    assert result == tmp_path / ".venv"


def test_find_venv_skips_file_not_dir(tmp_path):
    """Mutant killer: a file named .venv is not a venv (is_dir check)."""
    (tmp_path / ".venv").write_text("not a directory")
    result = _find_venv(tmp_path)
    assert result is None


def test_find_venv_cfg_required_even_with_subfolders(tmp_path):
    """Mutant killer: dir with bin/ but no pyvenv.cfg is not a venv."""
    d = tmp_path / ".venv"
    d.mkdir()
    (d / "bin").mkdir()
    result = _find_venv(tmp_path)
    assert result is None


# ── _has_python_project ────────────────────────────────────────────


def test_has_python_project_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    assert _has_python_project(tmp_path) is True


def test_has_python_project_setup_py(tmp_path):
    (tmp_path / "setup.py").write_text("")
    assert _has_python_project(tmp_path) is True


def test_has_python_project_setup_cfg(tmp_path):
    """Mutant killer: setup.cfg is also a Python project marker."""
    (tmp_path / "setup.cfg").write_text("")
    assert _has_python_project(tmp_path) is True


def test_has_python_project_pipfile(tmp_path):
    """Mutant killer: Pipfile is also a Python project marker."""
    (tmp_path / "Pipfile").write_text("")
    assert _has_python_project(tmp_path) is True


def test_has_python_project_requirements_txt(tmp_path):
    """Mutant killer: requirements.txt is also a Python project marker."""
    (tmp_path / "requirements.txt").write_text("")
    assert _has_python_project(tmp_path) is True


def test_has_python_project_none(tmp_path):
    assert _has_python_project(tmp_path) is False


def test_has_python_project_non_python_files_only(tmp_path):
    """Mutant killer: non-Python files do not trigger detection."""
    (tmp_path / "Cargo.toml").write_text("")
    (tmp_path / "package.json").write_text("")
    assert _has_python_project(tmp_path) is False


def test_has_python_project_multiple_markers(tmp_path):
    """Mutant killer: multiple markers still return True, not an error."""
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "setup.py").write_text("")
    assert _has_python_project(tmp_path) is True


# ── _has_ci_config ─────────────────────────────────────────────────


def test_has_ci_config_github(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    assert _has_ci_config(tmp_path) is True


def test_has_ci_config_gitlab(tmp_path):
    (tmp_path / ".gitlab-ci.yml").write_text("")
    assert _has_ci_config(tmp_path) is True


def test_has_ci_config_jenkinsfile(tmp_path):
    """Mutant killer: Jenkinsfile is a CI marker."""
    (tmp_path / "Jenkinsfile").write_text("")
    assert _has_ci_config(tmp_path) is True


def test_has_ci_config_circleci(tmp_path):
    """Mutant killer: .circleci directory is a CI marker."""
    (tmp_path / ".circleci").mkdir()
    assert _has_ci_config(tmp_path) is True


def test_has_ci_config_travis(tmp_path):
    """Mutant killer: .travis.yml is a CI marker."""
    (tmp_path / ".travis.yml").write_text("")
    assert _has_ci_config(tmp_path) is True


def test_has_ci_config_azure(tmp_path):
    """Mutant killer: azure-pipelines.yml is a CI marker."""
    (tmp_path / "azure-pipelines.yml").write_text("")
    assert _has_ci_config(tmp_path) is True


def test_has_ci_config_buildkite(tmp_path):
    """Mutant killer: .buildkite directory is a CI marker."""
    (tmp_path / ".buildkite").mkdir()
    assert _has_ci_config(tmp_path) is True


def test_has_ci_config_none(tmp_path):
    assert _has_ci_config(tmp_path) is False


def test_has_ci_config_unrelated_files(tmp_path):
    """Mutant killer: random YAML files don't count as CI config."""
    (tmp_path / "config.yml").write_text("")
    (tmp_path / ".github").mkdir()  # .github dir alone, without workflows/
    assert _has_ci_config(tmp_path) is False


# ── _missing_lockfiles ─────────────────────────────────────────────


def test_missing_lockfiles_pyproject_no_lock(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    result = _missing_lockfiles(tmp_path)
    assert len(result) == 1
    assert result[0][0] == "pyproject.toml"


def test_missing_lockfiles_pyproject_expected_locks():
    """Mutant killer: verify the expected lockfile list in the result."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = __import__("pathlib").Path(td)
        (root / "pyproject.toml").write_text("")
        result = _missing_lockfiles(root)
        assert result[0][1] == ["uv.lock", "poetry.lock"]


def test_missing_lockfiles_pyproject_with_uv_lock(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "uv.lock").write_text("")
    result = _missing_lockfiles(tmp_path)
    assert len(result) == 0


def test_missing_lockfiles_pyproject_with_poetry_lock(tmp_path):
    """Mutant killer: poetry.lock also satisfies pyproject.toml."""
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "poetry.lock").write_text("")
    result = _missing_lockfiles(tmp_path)
    assert len(result) == 0


def test_missing_lockfiles_empty_project(tmp_path):
    result = _missing_lockfiles(tmp_path)
    assert result == []


def test_missing_lockfiles_pipfile_no_lock(tmp_path):
    """Mutant killer: Pipfile without Pipfile.lock is flagged."""
    (tmp_path / "Pipfile").write_text("")
    result = _missing_lockfiles(tmp_path)
    manifests = [r[0] for r in result]
    assert "Pipfile" in manifests


def test_missing_lockfiles_pipfile_with_lock(tmp_path):
    """Mutant killer: Pipfile with Pipfile.lock is not flagged."""
    (tmp_path / "Pipfile").write_text("")
    (tmp_path / "Pipfile.lock").write_text("")
    result = _missing_lockfiles(tmp_path)
    manifests = [r[0] for r in result]
    assert "Pipfile" not in manifests


def test_missing_lockfiles_requirements_in_no_lock(tmp_path):
    """Mutant killer: requirements.in without requirements.txt is flagged."""
    (tmp_path / "requirements.in").write_text("")
    result = _missing_lockfiles(tmp_path)
    manifests = [r[0] for r in result]
    assert "requirements.in" in manifests


def test_missing_lockfiles_requirements_in_with_txt(tmp_path):
    """Mutant killer: requirements.in with requirements.txt is not flagged."""
    (tmp_path / "requirements.in").write_text("")
    (tmp_path / "requirements.txt").write_text("")
    result = _missing_lockfiles(tmp_path)
    manifests = [r[0] for r in result]
    assert "requirements.in" not in manifests


def test_missing_lockfiles_package_json_no_lock(tmp_path):
    """Mutant killer: package.json without any node lockfile is flagged."""
    (tmp_path / "package.json").write_text("{}")
    result = _missing_lockfiles(tmp_path)
    manifests = [r[0] for r in result]
    assert "package.json" in manifests


def test_missing_lockfiles_package_json_with_yarn(tmp_path):
    """Mutant killer: yarn.lock satisfies package.json."""
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "yarn.lock").write_text("")
    result = _missing_lockfiles(tmp_path)
    manifests = [r[0] for r in result]
    assert "package.json" not in manifests


def test_missing_lockfiles_package_json_with_pnpm(tmp_path):
    """Mutant killer: pnpm-lock.yaml satisfies package.json."""
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "pnpm-lock.yaml").write_text("")
    result = _missing_lockfiles(tmp_path)
    manifests = [r[0] for r in result]
    assert "package.json" not in manifests


def test_missing_lockfiles_cargo_no_lock(tmp_path):
    """Mutant killer: Cargo.toml without Cargo.lock is flagged."""
    (tmp_path / "Cargo.toml").write_text("")
    result = _missing_lockfiles(tmp_path)
    manifests = [r[0] for r in result]
    assert "Cargo.toml" in manifests


def test_missing_lockfiles_cargo_with_lock(tmp_path):
    """Mutant killer: Cargo.toml with Cargo.lock is not flagged."""
    (tmp_path / "Cargo.toml").write_text("")
    (tmp_path / "Cargo.lock").write_text("")
    result = _missing_lockfiles(tmp_path)
    manifests = [r[0] for r in result]
    assert "Cargo.toml" not in manifests


def test_missing_lockfiles_go_no_lock(tmp_path):
    """Mutant killer: go.mod without go.sum is flagged."""
    (tmp_path / "go.mod").write_text("")
    result = _missing_lockfiles(tmp_path)
    manifests = [r[0] for r in result]
    assert "go.mod" in manifests


def test_missing_lockfiles_go_with_lock(tmp_path):
    """Mutant killer: go.mod with go.sum is not flagged."""
    (tmp_path / "go.mod").write_text("")
    (tmp_path / "go.sum").write_text("")
    result = _missing_lockfiles(tmp_path)
    manifests = [r[0] for r in result]
    assert "go.mod" not in manifests


def test_missing_lockfiles_multiple_manifests(tmp_path):
    """Mutant killer: multiple missing manifests all reported."""
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "go.mod").write_text("")
    result = _missing_lockfiles(tmp_path)
    manifests = [r[0] for r in result]
    assert "pyproject.toml" in manifests
    assert "package.json" in manifests
    assert "go.mod" in manifests


def test_missing_lockfiles_result_tuples_structure(tmp_path):
    """Mutant killer: result is list of (manifest_name, expected_locks_list)."""
    (tmp_path / "go.mod").write_text("")
    result = _missing_lockfiles(tmp_path)
    assert len(result) == 1
    manifest, locks = result[0]
    assert manifest == "go.mod"
    assert locks == ["go.sum"]


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


def test_stale_lockfiles_lock_only_no_manifest(tmp_path):
    """Mutant killer: lockfile without manifest is not flagged."""
    (tmp_path / "uv.lock").write_text("")
    result = _stale_lockfiles(tmp_path)
    assert result == []


def test_stale_lockfiles_manifest_only_no_lock(tmp_path):
    """Mutant killer: manifest without lockfile is not flagged (that's _missing_lockfiles)."""
    (tmp_path / "pyproject.toml").write_text("")
    result = _stale_lockfiles(tmp_path)
    assert result == []


def test_stale_lockfiles_poetry_stale(tmp_path):
    """Mutant killer: poetry.lock stale relative to pyproject.toml."""
    lock = tmp_path / "poetry.lock"
    manifest = tmp_path / "pyproject.toml"
    lock.write_text("")
    time.sleep(0.05)
    manifest.write_text("")
    result = _stale_lockfiles(tmp_path)
    stale_pairs = [(m, lk) for m, lk in result]
    assert ("pyproject.toml", "poetry.lock") in stale_pairs


def test_stale_lockfiles_node_stale(tmp_path):
    """Mutant killer: package-lock.json stale relative to package.json."""
    lock = tmp_path / "package-lock.json"
    manifest = tmp_path / "package.json"
    lock.write_text("")
    time.sleep(0.05)
    manifest.write_text("{}")
    result = _stale_lockfiles(tmp_path)
    stale_pairs = [(m, lk) for m, lk in result]
    assert ("package.json", "package-lock.json") in stale_pairs


def test_stale_lockfiles_cargo_stale(tmp_path):
    """Mutant killer: Cargo.lock stale relative to Cargo.toml."""
    lock = tmp_path / "Cargo.lock"
    manifest = tmp_path / "Cargo.toml"
    lock.write_text("")
    time.sleep(0.05)
    manifest.write_text("")
    result = _stale_lockfiles(tmp_path)
    assert ("Cargo.toml", "Cargo.lock") in result


def test_stale_lockfiles_go_stale(tmp_path):
    """Mutant killer: go.sum stale relative to go.mod."""
    lock = tmp_path / "go.sum"
    manifest = tmp_path / "go.mod"
    lock.write_text("")
    time.sleep(0.05)
    manifest.write_text("")
    result = _stale_lockfiles(tmp_path)
    assert ("go.mod", "go.sum") in result


def test_stale_lockfiles_result_is_manifest_lock_pair(tmp_path):
    """Mutant killer: result tuple order is (manifest_name, lock_name)."""
    lock = tmp_path / "Cargo.lock"
    manifest = tmp_path / "Cargo.toml"
    lock.write_text("")
    time.sleep(0.05)
    manifest.write_text("")
    result = _stale_lockfiles(tmp_path)
    assert len(result) >= 1
    manifest_name, lock_name = result[-1]  # Cargo may not be first
    # Find the Cargo entry
    cargo_entries = [(m, lk) for m, lk in result if lk == "Cargo.lock"]
    assert len(cargo_entries) == 1
    assert cargo_entries[0] == ("Cargo.toml", "Cargo.lock")


def test_stale_lockfiles_same_mtime_not_stale(tmp_path):
    """Mutant killer: equal mtime -> not stale (uses > not >=)."""
    lock = tmp_path / "go.sum"
    manifest = tmp_path / "go.mod"
    lock.write_text("")
    manifest.write_text("")
    # Set both to the same mtime
    mtime = time.time()
    os.utime(lock, (mtime, mtime))
    os.utime(manifest, (mtime, mtime))
    result = _stale_lockfiles(tmp_path)
    go_entries = [(m, lk) for m, lk in result if lk == "go.sum"]
    assert go_entries == []


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


def test_is_global_install_with_short_target_flag(tmp_path):
    """Mutant killer: -t flag also prevents global install detection."""
    assert _is_global_install("pip install -t /foo requests", tmp_path) is False


def test_is_global_install_with_prefix_flag(tmp_path):
    """Mutant killer: --prefix flag also prevents global install detection."""
    assert _is_global_install("pip install --prefix /usr/local requests", tmp_path) is False


def test_is_global_install_non_install_command(tmp_path):
    assert _is_global_install("git commit -m 'msg'", tmp_path) is False


def test_is_global_install_pip_freeze_not_install(tmp_path):
    """Mutant killer: pip freeze is not an install command."""
    assert _is_global_install("pip freeze", tmp_path) is False


def test_is_global_install_pip_list_not_install(tmp_path):
    """Mutant killer: pip list is not an install command."""
    assert _is_global_install("pip list", tmp_path) is False


def test_is_global_install_leading_whitespace(tmp_path):
    """Mutant killer: leading whitespace is handled by the regex."""
    assert _is_global_install("  pip install requests", tmp_path) is True


def test_is_global_install_empty_string(tmp_path):
    """Mutant killer: empty command returns False."""
    assert _is_global_install("", tmp_path) is False


def test_is_global_install_venv_prevents_detection(tmp_path):
    """Mutant killer: any valid venv in _VENV_DIRS order prevents global."""
    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("")
    assert _is_global_install("pip install requests", tmp_path) is False


def test_is_global_install_uv_pip_with_target(tmp_path):
    """Mutant killer: uv pip install --target is not global."""
    assert _is_global_install("uv pip install --target /foo pkg", tmp_path) is False


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


def test_load_json_string_value(tmp_path):
    """Mutant killer: JSON string (not dict) returns None."""
    p = tmp_path / "str.json"
    p.write_text('"hello"')
    result = _load_json(p)
    assert result is None


def test_load_json_number_value(tmp_path):
    """Mutant killer: JSON number (not dict) returns None."""
    p = tmp_path / "num.json"
    p.write_text("42")
    result = _load_json(p)
    assert result is None


def test_load_json_null_value(tmp_path):
    """Mutant killer: JSON null (not dict) returns None."""
    p = tmp_path / "null.json"
    p.write_text("null")
    result = _load_json(p)
    assert result is None


def test_load_json_boolean_value(tmp_path):
    """Mutant killer: JSON boolean (not dict) returns None."""
    p = tmp_path / "bool.json"
    p.write_text("true")
    result = _load_json(p)
    assert result is None


def test_load_json_empty_dict(tmp_path):
    """Mutant killer: empty dict is a valid dict, should be returned."""
    p = tmp_path / "empty.json"
    p.write_text("{}")
    result = _load_json(p)
    assert result == {}


def test_load_json_nested_dict(tmp_path):
    """Mutant killer: nested dict structure is preserved."""
    p = tmp_path / "nested.json"
    p.write_text('{"a": {"b": 1}}')
    result = _load_json(p)
    assert result == {"a": {"b": 1}}


def test_load_json_empty_file(tmp_path):
    """Mutant killer: empty file -> JSONDecodeError -> None."""
    p = tmp_path / "empty.json"
    p.write_text("")
    result = _load_json(p)
    assert result is None


def test_load_json_directory_path(tmp_path):
    """Mutant killer: passing a directory path -> OSError -> None."""
    result = _load_json(tmp_path)
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


def test_record_dep_event_exact_count(tmp_path):
    """Mutant killer: verify exact recent_count after N events."""
    with patch("lintgate._dep_health_helpers.DEP_HEALTH_DIR", tmp_path):
        result = _record_dep_event("/test/project", "install")
        assert result is not None
        assert result["recent_count"] == 1
        result = _record_dep_event("/test/project", "install")
        assert result is not None
        assert result["recent_count"] == 2
        result = _record_dep_event("/test/project", "install")
        assert result is not None
        assert result["recent_count"] == 3


def test_record_dep_event_not_churning_below_threshold(tmp_path):
    """Mutant killer: 4 events is below churn threshold of 5."""
    with patch("lintgate._dep_health_helpers.DEP_HEALTH_DIR", tmp_path):
        for _ in range(4):
            result = _record_dep_event("/test/project", "install")
    assert result is not None
    assert result["is_churning"] is False
    assert result["recent_count"] == 4


def test_record_dep_event_churning_at_exactly_5(tmp_path):
    """Mutant killer: exactly 5 events triggers churn (>= 5)."""
    with patch("lintgate._dep_health_helpers.DEP_HEALTH_DIR", tmp_path):
        for _ in range(5):
            result = _record_dep_event("/test/project", "install")
    assert result is not None
    assert result["is_churning"] is True
    assert result["recent_count"] == 5


def test_record_dep_event_writes_valid_json(tmp_path):
    """Mutant killer: verify the written file is valid JSON with expected fields."""
    with patch("lintgate._dep_health_helpers.DEP_HEALTH_DIR", tmp_path):
        _record_dep_event("/test/project", "install")
    # Find the written file
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert "events" in data
    assert "updated_at" in data
    assert "project" in data
    assert data["project"] == "/test/project"
    assert len(data["events"]) == 1
    assert data["events"][0]["kind"] == "install"


def test_record_dep_event_caps_at_100(tmp_path):
    """Mutant killer: events list is capped at 100."""
    with patch("lintgate._dep_health_helpers.DEP_HEALTH_DIR", tmp_path):
        for i in range(105):
            _record_dep_event("/test/project", f"event_{i}")
    files = list(tmp_path.iterdir())
    data = json.loads(files[0].read_text())
    assert len(data["events"]) == 100
    # Last event should be the most recent
    assert data["events"][-1]["kind"] == "event_104"
    # First event should be event_5 (0-4 dropped)
    assert data["events"][0]["kind"] == "event_5"


def test_record_dep_event_different_projects_separate_files(tmp_path):
    """Mutant killer: different projects get different history files."""
    with patch("lintgate._dep_health_helpers.DEP_HEALTH_DIR", tmp_path):
        _record_dep_event("/project/a", "install")
        _record_dep_event("/project/b", "install")
    files = list(tmp_path.iterdir())
    assert len(files) == 2


def test_record_dep_event_preserves_change_kind(tmp_path):
    """Mutant killer: the change_kind argument is stored in events."""
    with patch("lintgate._dep_health_helpers.DEP_HEALTH_DIR", tmp_path):
        _record_dep_event("/test/project", "uninstall")
    files = list(tmp_path.iterdir())
    data = json.loads(files[0].read_text())
    assert data["events"][0]["kind"] == "uninstall"


def test_record_dep_event_creates_directory(tmp_path):
    """Mutant killer: DEP_HEALTH_DIR is created if it doesn't exist."""
    nested = tmp_path / "a" / "b" / "c"
    with patch("lintgate._dep_health_helpers.DEP_HEALTH_DIR", nested):
        result = _record_dep_event("/test/project", "install")
    assert nested.is_dir()
    assert result is not None


# ── _load_dep_history ──────────────────────────────────────────────


def test_load_dep_history_after_record(tmp_path):
    """Mutant killer: _load_dep_history reads back what _record_dep_event wrote."""
    with patch("lintgate._dep_health_helpers.DEP_HEALTH_DIR", tmp_path):
        _record_dep_event("/test/project", "install")
        result = _load_dep_history("/test/project")
    assert result is not None
    assert "events" in result
    assert len(result["events"]) == 1


def test_load_dep_history_no_record(tmp_path):
    """Mutant killer: _load_dep_history returns None when no record exists."""
    with patch("lintgate._dep_health_helpers.DEP_HEALTH_DIR", tmp_path):
        result = _load_dep_history("/nonexistent/project")
    assert result is None


def test_load_dep_history_uses_same_hash_key(tmp_path):
    """Mutant killer: same project path returns the same data."""
    with patch("lintgate._dep_health_helpers.DEP_HEALTH_DIR", tmp_path):
        _record_dep_event("/test/project", "install")
        _record_dep_event("/test/project", "uninstall")
        result = _load_dep_history("/test/project")
    assert result is not None
    assert len(result["events"]) == 2


# ── _VENV_DIRS constant ───────────────────────────────────────────


def test_venv_dirs_values():
    assert ".venv" in _VENV_DIRS
    assert "venv" in _VENV_DIRS
    assert "env" in _VENV_DIRS
    assert ".env" in _VENV_DIRS


def test_venv_dirs_length():
    """Mutant killer: exactly 4 venv dir names."""
    assert len(_VENV_DIRS) == 4


def test_venv_dirs_order():
    """Mutant killer: order matters for priority in _find_venv."""
    assert _VENV_DIRS == (".venv", "venv", "env", ".env")

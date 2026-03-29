"""Tests for mcp_tools/_test_regeneration_apply.py helper functions."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from mcp_tools._test_regeneration_apply import (
    _VALIDATION_FILE,
    _load_validation,
    _promote_generated,
    _quarantine_files,
    impl_rebuild_apply,
    persist_validation,
)


def _load_tool_result(json_str):
    import json as _j
    import os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f:

            return _j.loads(f.read())
    return r


# ---------------------------------------------------------------------------
# persist_validation
# ---------------------------------------------------------------------------


class TestPersistValidation:
    def test_writes_validation_file(self, tmp_path):
        gates = {"syntax": True, "tests_pass": True}
        path = persist_validation(str(tmp_path), gates, ready=True)
        assert os.path.isfile(path)
        with open(path) as f:
            data = json.load(f)
        assert data["ready_to_apply"] is True
        assert data["review_ready_to_apply"] is False
        assert data["gates"] == gates

    def test_custom_validation_path(self, tmp_path):
        custom = str(tmp_path / "custom" / "val.json")
        path = persist_validation(str(tmp_path), {"ok": True}, ready=False, validation_path=custom)
        assert path == custom
        assert os.path.isfile(custom)

    def test_review_ready_to_apply_flag(self, tmp_path):
        path = persist_validation(str(tmp_path), {}, ready=False, review_ready_to_apply=True)
        with open(path) as f:
            data = json.load(f)
        assert data["ready_to_apply"] is False
        assert data["review_ready_to_apply"] is True


# ---------------------------------------------------------------------------
# _load_validation
# ---------------------------------------------------------------------------


class TestLoadValidation:
    def test_loads_valid_file(self, tmp_path):
        vpath = tmp_path / _VALIDATION_FILE
        vpath.parent.mkdir(parents=True, exist_ok=True)
        data = {"ready_to_apply": True, "gates": {}, "review_ready_to_apply": False}
        with open(vpath, "w") as f:
            json.dump(data, f)
        result = _load_validation(str(tmp_path))
        assert result is not None
        assert result["ready_to_apply"] is True

    def test_returns_none_when_missing(self, tmp_path):
        assert _load_validation(str(tmp_path)) is None

    def test_returns_none_on_invalid_json(self, tmp_path):
        vpath = tmp_path / _VALIDATION_FILE
        vpath.parent.mkdir(parents=True, exist_ok=True)
        vpath.write_text("not json{{{")
        assert _load_validation(str(tmp_path)) is None


# ---------------------------------------------------------------------------
# _quarantine_files
# ---------------------------------------------------------------------------


class TestQuarantineFiles:
    def test_dry_run_does_not_move_files(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        old_test = tests_dir / "test_old.py"
        old_test.write_text("# old test")

        plan = SimpleNamespace(quarantine_test_files=["tests/test_old.py"])
        actions: list[dict] = []
        result = _quarantine_files(plan, str(tmp_path), True, actions)

        assert len(result) == 1
        assert result[0]["action"] == "quarantine"
        assert result[0]["source"] == "tests/test_old.py"
        # File should still exist in dry run
        assert old_test.exists()

    def test_moves_files_when_not_dry_run(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        old_test = tests_dir / "test_old.py"
        old_test.write_text("# old")

        plan = SimpleNamespace(quarantine_test_files=["tests/test_old.py"])
        actions: list[dict] = []
        result = _quarantine_files(plan, str(tmp_path), False, actions)

        assert len(result) == 1
        assert result[0]["action"] == "quarantine"
        assert not old_test.exists()
        quarantined = tmp_path / "tests" / "quarantine" / "test_old.py"
        assert quarantined.exists()

    def test_preserves_subdirectory_structure(self, tmp_path):
        tests_dir = tmp_path / "tests" / "api"
        tests_dir.mkdir(parents=True)
        old_test = tests_dir / "test_utils.py"
        old_test.write_text("# api test")

        plan = SimpleNamespace(quarantine_test_files=["tests/api/test_utils.py"])
        actions: list[dict] = []
        result = _quarantine_files(plan, str(tmp_path), False, actions)

        assert len(result) == 1
        quarantined = tmp_path / "tests" / "quarantine" / "api" / "test_utils.py"
        assert quarantined.exists()


# ---------------------------------------------------------------------------
# _promote_generated
# ---------------------------------------------------------------------------


class TestPromoteGenerated:
    def test_promotes_py_files_from_generated(self, tmp_path):
        gen_dir = tmp_path / "tests" / "generated"
        gen_dir.mkdir(parents=True)
        (gen_dir / "test_new.py").write_text("# new test")
        (gen_dir / "not_a_test.txt").write_text("# ignored")

        actions: list[dict] = []
        result = _promote_generated(str(tmp_path), False, actions)

        assert len(result) == 1
        assert result[0]["action"] == "promote"
        assert result[0]["source"] == "tests/generated/test_new.py"
        assert result[0]["destination"] == "tests/test_new.py"
        assert (tmp_path / "tests" / "test_new.py").exists()
        assert not (gen_dir / "test_new.py").exists()

    def test_dry_run_does_not_move(self, tmp_path):
        gen_dir = tmp_path / "tests" / "generated"
        gen_dir.mkdir(parents=True)
        (gen_dir / "test_x.py").write_text("# x")

        actions: list[dict] = []
        result = _promote_generated(str(tmp_path), True, actions)

        assert len(result) == 1
        assert result[0]["action"] == "promote"
        # File should still exist in generated
        assert (gen_dir / "test_x.py").exists()

    def test_no_generated_dir(self, tmp_path):
        actions: list[dict] = []
        result = _promote_generated(str(tmp_path), False, actions)
        assert result == []

    def test_removes_empty_generated_dir(self, tmp_path):
        gen_dir = tmp_path / "tests" / "generated"
        gen_dir.mkdir(parents=True)
        (gen_dir / "test_y.py").write_text("# y")

        actions: list[dict] = []
        _promote_generated(str(tmp_path), False, actions)

        assert not gen_dir.exists()


# ---------------------------------------------------------------------------
# impl_rebuild_apply
# ---------------------------------------------------------------------------


class TestImplRebuildApply:
    def _make_helpers(self, project_root):
        return {
            "_validate_project_root": lambda path: project_root,
            "_json_dumps": lambda data, output_mode="compact": json.dumps(data),
        }

    def test_no_manifest_returns_error(self, tmp_path):
        helpers = self._make_helpers(str(tmp_path))
        with patch(
            "lintgate.specification.test_regeneration_strategy.load_manifest",
            return_value=None,
        ):
            result_str = impl_rebuild_apply(helpers, str(tmp_path))
            result = _load_tool_result(result_str)
            assert "error" in result
            assert "manifest" in result["error"].lower()

    def test_no_validation_returns_error(self, tmp_path):
        helpers = self._make_helpers(str(tmp_path))
        plan = SimpleNamespace(quarantine_test_files=[], files=[])
        with patch(
            "lintgate.specification.test_regeneration_strategy.load_manifest",
            return_value=plan,
        ):
            result_str = impl_rebuild_apply(helpers, str(tmp_path))
            result = _load_tool_result(result_str)
            assert "error" in result
            assert "validation" in result["error"].lower()

    def test_dry_run_with_valid_manifest_and_validation(self, tmp_path):
        helpers = self._make_helpers(str(tmp_path))

        # Create validation file
        vpath = tmp_path / _VALIDATION_FILE
        vpath.parent.mkdir(parents=True, exist_ok=True)
        with open(vpath, "w") as f:
            json.dump({"ready_to_apply": True, "gates": {}, "review_ready_to_apply": False}, f)

        plan = SimpleNamespace(
            quarantine_test_files=[],
        )

        with patch(
            "lintgate.specification.test_regeneration_strategy.load_manifest",
            return_value=plan,
        ):
            result_str = impl_rebuild_apply(helpers, str(tmp_path), dry_run=True)
            result = _load_tool_result(result_str)
            assert result["dry_run"] is True
            assert result["quarantined"] == 0
            assert result["promoted"] == 0
            assert "next_actions" in result

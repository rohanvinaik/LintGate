"""Test channel edge cases, branch coverage, and helper function tests.

Split from test_test_channel.py to stay under the 400-line limit.

Covers:
- Skeleton generation
- _discover_fallback_test_targets
- _select_tests_to_run
- _filter_to_source_packages (core + edge cases)
- _parse_coverage_settings edge cases
- run_tests error handling
- TestChannel.execute ephemeral JSON cleanup
"""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

from lintgate.channels.test_channel import (
    TestChannel,
    TestRunResult,
    _discover_fallback_test_targets,
    _filter_to_source_packages,
    _parse_coverage_settings,
    _select_tests_to_run,
    run_tests,
)
from lintgate.controlplane.skeleton_generator import (
    generate_test_path,
    generate_test_skeleton,
)
from lintgate.controlplane.types import (
    ControlPlaneConfig,
    SupervisionEvent,
)
from lintgate.types import ChangeClassification

# ── Skeleton generation tests ────────────────────────────────────────────


def test_skeleton_generates_valid_python(tmp_path: Path) -> None:
    import ast

    src = tmp_path / "calculator.py"
    src.write_text(
        textwrap.dedent("""\
        def add(a: int, b: int) -> int:
            return a + b

        def divide(a: float, b: float) -> float:
            if b == 0:
                raise ValueError("Cannot divide by zero")
            return a / b
    """)
    )
    skeleton = generate_test_skeleton(str(src), project_root=str(tmp_path))
    assert skeleton
    ast.parse(skeleton)


def test_skeleton_includes_imports(tmp_path: Path) -> None:
    src = tmp_path / "module.py"
    src.write_text("def process(data: str) -> str:\n    return data")
    skeleton = generate_test_skeleton(str(src), project_root=str(tmp_path))
    assert "import pytest" in skeleton


def test_skeleton_includes_function_tests(tmp_path: Path) -> None:
    src = tmp_path / "module.py"
    src.write_text(
        "def validate(data: str) -> str:\n    if not data:\n        raise ValueError('empty')\n    return data"
    )
    skeleton = generate_test_skeleton(str(src), project_root=str(tmp_path))
    assert "test_validate" in skeleton


def test_skeleton_includes_class_tests(tmp_path: Path) -> None:
    src = tmp_path / "models.py"
    src.write_text(
        textwrap.dedent("""\
        from dataclasses import dataclass

        @dataclass
        class Config:
            name: str = "default"
            value: int = 0
    """)
    )
    skeleton = generate_test_skeleton(str(src), project_root=str(tmp_path))
    assert "TestConfig" in skeleton or "test_" in skeleton


def test_skeleton_for_empty_file(tmp_path: Path) -> None:
    src = tmp_path / "empty.py"
    src.write_text("")
    skeleton = generate_test_skeleton(str(src), project_root=str(tmp_path))
    assert "placeholder" in skeleton.lower() or "test_" in skeleton


def test_generate_test_path(tmp_path: Path) -> None:
    src = tmp_path / "lintgate" / "types.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("")
    path = generate_test_path(str(src), str(tmp_path))
    assert "test_types.py" in path
    assert "tests" in path


# ── _discover_fallback_test_targets ──────────────────────────────────────


def test_fallback_discovers_test_dirs(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass\n")
    targets = _discover_fallback_test_targets(str(tmp_path))
    assert any("tests" in t for t in targets)


def test_fallback_discovers_root_test_files(tmp_path: Path) -> None:
    (tmp_path / "test_root.py").write_text("def test_r(): pass\n")
    targets = _discover_fallback_test_targets(str(tmp_path))
    assert any("test_root.py" in t for t in targets)


def test_fallback_empty_project(tmp_path: Path) -> None:
    assert _discover_fallback_test_targets(str(tmp_path)) == []


def test_fallback_discovers_test_dir_over_root_files(tmp_path: Path) -> None:
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "test_a.py").write_text("def test_a(): pass\n")
    (tmp_path / "test_root.py").write_text("def test_r(): pass\n")
    targets = _discover_fallback_test_targets(str(tmp_path))
    assert any("test" in t for t in targets)
    assert not any("test_root.py" in t for t in targets)


def test_fallback_skips_non_file_glob_match(tmp_path: Path) -> None:
    (tmp_path / "test_fake.py").mkdir()
    assert _discover_fallback_test_targets(str(tmp_path)) == []


def test_fallback_loop_continues_past_non_file(tmp_path: Path) -> None:
    (tmp_path / "test_aaa_dir.py").mkdir()
    (tmp_path / "test_zzz_real.py").write_text("def test_ok(): pass\n")
    targets = _discover_fallback_test_targets(str(tmp_path))
    assert len(targets) == 1
    assert "test_zzz_real.py" in targets[0]


def test_fallback_discovers_both_test_dirs(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_a(): pass\n")
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "test_b.py").write_text("def test_b(): pass\n")
    targets = _discover_fallback_test_targets(str(tmp_path))
    assert len(targets) == 2


# ── _select_tests_to_run ────────────────────────────────────────────────


def test_select_returns_impacted_when_present() -> None:
    result = _select_tests_to_run(
        ["tests/test_a.py"],
        "/tmp",
        {"symbol_enabled": True},
        "mcp",
        [],
    )
    assert result == ["tests/test_a.py"]


def test_select_returns_empty_when_no_cov_cfg() -> None:
    assert _select_tests_to_run([], "/tmp", None, "mcp", []) == []


def test_select_returns_empty_when_symbol_disabled() -> None:
    assert _select_tests_to_run([], "/tmp", {"symbol_enabled": False}, "mcp", []) == []


def test_select_returns_empty_on_hook_surface() -> None:
    assert _select_tests_to_run([], "/tmp", {"symbol_enabled": True}, "hook", []) == []


# ── _filter_to_source_packages ───────────────────────────────────────────


def test_filter_to_source_packages_filters_correctly(tmp_path: Path) -> None:
    root = str(tmp_path)
    changed = [
        f"{root}/lintgate/config.py",
        f"{root}/mcp_tools/tools.py",
        f"{root}/tests/test_x.py",
        f"{root}/.claude/lintgate.yaml",
    ]
    result = _filter_to_source_packages(changed, ["lintgate", "mcp_tools"], root)
    assert len(result) == 2


def test_filter_to_source_packages_empty_packages() -> None:
    changed = ["/a/b.py"]
    assert _filter_to_source_packages(changed, [], "/") == changed


def test_filter_to_source_packages_none_packages() -> None:
    changed = ["/a/b.py"]
    assert _filter_to_source_packages(changed, None, "/") == changed


def test_filter_to_source_packages_matches_files_in_package(tmp_path: Path) -> None:
    changed = [
        str(tmp_path / "lintgate" / "channels" / "test_channel.py"),
        str(tmp_path / "lintgate" / "config.py"),
    ]
    result = _filter_to_source_packages(changed, ["lintgate"], str(tmp_path))
    assert len(result) == 2


def test_filter_to_source_packages_excludes_outside_packages(tmp_path: Path) -> None:
    changed = [
        str(tmp_path / "lintgate" / "config.py"),
        str(tmp_path / "tests" / "test_config.py"),
        str(tmp_path / "docs" / "readme.md"),
    ]
    result = _filter_to_source_packages(changed, ["lintgate"], str(tmp_path))
    assert len(result) == 1


def test_filter_to_source_packages_exact_match(tmp_path: Path) -> None:
    changed = [str(tmp_path / "mypkg")]
    result = _filter_to_source_packages(changed, ["mypkg"], str(tmp_path))
    assert changed[0] in result


def test_filter_to_source_packages_empty_returns_all() -> None:
    changed = ["/a/b.py", "/c/d.py"]
    assert _filter_to_source_packages(changed, [], "/root") == changed


def test_filter_to_source_packages_multiple_packages(tmp_path: Path) -> None:
    changed = [
        str(tmp_path / "lintgate" / "config.py"),
        str(tmp_path / "mcp_tools" / "server.py"),
        str(tmp_path / "docs" / "notes.txt"),
    ]
    result = _filter_to_source_packages(changed, ["lintgate", "mcp_tools"], str(tmp_path))
    assert len(result) == 2


def test_filter_to_source_packages_skips_unrelatable_paths() -> None:
    if sys.platform == "win32":
        changed = ["D:\\other\\file.py"]
        result = _filter_to_source_packages(changed, ["src"], "C:\\project")
        assert result == []
    else:
        changed = ["/completely/different/path.py"]
        result = _filter_to_source_packages(changed, ["src"], "/project")
        assert changed[0] not in result


def test_filter_to_source_packages_value_error(tmp_path: Path) -> None:
    files = ["/some/file.py", str(tmp_path / "lintgate" / "ok.py")]
    with patch(
        "lintgate.channels._test_channel_symbol_gate.os.path.relpath", side_effect=ValueError
    ):
        result = _filter_to_source_packages(files, ["lintgate"], str(tmp_path))
    assert result == []


# ── _parse_coverage_settings edge cases ──────────────────────────────────


def test_parse_coverage_settings_str_source_packages() -> None:
    result = _parse_coverage_settings({"source_packages": "mypackage"}, "mcp")
    assert result["source_packages"] == ["mypackage"]


def test_parse_coverage_settings_list_source_packages() -> None:
    result = _parse_coverage_settings({"source_packages": ["a", "b"]}, "mcp")
    assert result["source_packages"] == ["a", "b"]


def test_parse_coverage_settings_fallback_source_packages() -> None:
    result = _parse_coverage_settings({}, "mcp")
    assert result["source_packages"] == ["lintgate", "mcp_tools"]


def test_parse_coverage_settings_string_with_whitespace() -> None:
    result = _parse_coverage_settings({"source_packages": "  mypackage  "}, "hook")
    assert result["source_packages"] == ["mypackage"]


def test_parse_coverage_settings_empty_string_falls_back() -> None:
    result = _parse_coverage_settings({"source_packages": "   "}, "hook")
    assert result["source_packages"] == ["lintgate", "mcp_tools"]


# ── run_tests edge cases ────────────────────────────────────────────────


def test_run_tests_os_error(tmp_path: Path) -> None:
    with patch(
        "lintgate.channels._test_channel_runner.subprocess.run",
        side_effect=OSError("no pytest"),
    ):
        result = run_tests(str(tmp_path), ["tests/test_x.py"])
    assert result.passed == 0
    assert result.failed == 0
    assert not result.timed_out


# ── TestChannel.execute: ephemeral JSON cleanup ─────────────────────────


@patch("lintgate.channels._test_channel_runner.subprocess.run")
def test_execute_cleans_up_ephemeral_coverage_json(
    mock_run: MagicMock,
    tmp_path: Path,
) -> None:
    import json
    import tempfile

    fd, ephemeral_path = tempfile.mkstemp(prefix="lintgate_cov_json_", suffix=".json")
    os.close(fd)
    Path(ephemeral_path).write_text(json.dumps({"files": {}}))

    fake_result = TestRunResult(
        passed=1,
        coverage_json_path=ephemeral_path,
        coverage_json_ephemeral=True,
    )
    src = tmp_path / "app.py"
    src.write_text("x = 1")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_x(): pass")
    mock_run.return_value = MagicMock(stdout="1 passed in 0.01s", stderr="", returncode=0)

    event = SupervisionEvent(
        project_root=str(tmp_path),
        tool_name="Edit",
        files_changed=[str(src)],
        change_classification=ChangeClassification(
            files_changed=[str(src)],
            change_kind="logic",
            risk_level="moderate",
        ),
    )
    with patch("lintgate.channels._test_channel_runner.run_tests", return_value=fake_result):
        TestChannel().execute(event, ControlPlaneConfig())
    assert not Path(ephemeral_path).exists()


@patch("lintgate.channels._test_channel_runner.subprocess.run")
def test_execute_skips_cleanup_when_not_ephemeral(
    mock_run: MagicMock,
    tmp_path: Path,
) -> None:
    import json
    import tempfile

    fd, persistent_path = tempfile.mkstemp(prefix="lintgate_cov_json_", suffix=".json")
    os.close(fd)
    Path(persistent_path).write_text(json.dumps({"files": {}}))

    fake_result = TestRunResult(
        passed=1,
        coverage_json_path=persistent_path,
        coverage_json_ephemeral=False,
    )
    src = tmp_path / "app.py"
    src.write_text("x = 1")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_x(): pass")
    mock_run.return_value = MagicMock(stdout="1 passed in 0.01s", stderr="", returncode=0)

    event = SupervisionEvent(
        project_root=str(tmp_path),
        tool_name="Edit",
        files_changed=[str(src)],
        change_classification=ChangeClassification(
            files_changed=[str(src)],
            change_kind="logic",
            risk_level="moderate",
        ),
    )
    with patch("lintgate.channels._test_channel_runner.run_tests", return_value=fake_result):
        TestChannel().execute(event, ControlPlaneConfig())
    assert Path(persistent_path).exists()
    os.unlink(persistent_path)

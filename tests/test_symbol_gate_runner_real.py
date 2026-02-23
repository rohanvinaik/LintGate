"""Real integration tests for symbol_gate_runner.py uncovered code paths.

No mocks — exercises actual functions with crafted inputs.
Covers: _normalize filtering (lines 104, 109), working-tree git path (line 138),
and run_symbol_gate reporting branches (lines 193-203, 206).
"""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

from lintgate.symbol_gate_runner import (
    collect_changed_python_files,
    run_symbol_gate,
)

if TYPE_CHECKING:
    from pathlib import Path


# ── collect_changed_python_files: _normalize filtering ───────────────


class TestCollectNormalizeFiltering:
    def test_non_py_files_filtered_out(self, tmp_path: Path) -> None:
        """Non-.py files passed via explicit_files are skipped (line 104)."""
        py = tmp_path / "mod.py"
        py.write_text("pass\n")
        txt = tmp_path / "notes.txt"
        txt.write_text("hello\n")
        result = collect_changed_python_files(str(tmp_path), explicit_files=[str(py), str(txt)])
        assert len(result) == 1
        assert result[0] == str(py.resolve())

    def test_duplicate_paths_deduplicated(self, tmp_path: Path) -> None:
        """Same file listed twice is returned once (line 109)."""
        f = tmp_path / "mod.py"
        f.write_text("pass\n")
        result = collect_changed_python_files(str(tmp_path), explicit_files=[str(f), str(f)])
        assert len(result) == 1

    def test_relative_and_absolute_dedup(self, tmp_path: Path) -> None:
        """Relative + absolute path for same file resolves to one entry."""
        f = tmp_path / "mod.py"
        f.write_text("pass\n")
        result = collect_changed_python_files(str(tmp_path), explicit_files=[str(f), "mod.py"])
        assert len(result) == 1


# ── collect_changed_python_files: working-tree git path ──────────────


class TestCollectWorkingTreePath:
    def test_working_tree_changes_detected(self, tmp_path: Path) -> None:
        """Modified .py file in working tree is collected (line 138)."""
        subprocess.run(
            ["git", "init"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        f = tmp_path / "mod.py"
        f.write_text("x = 1\n")
        subprocess.run(
            ["git", "add", "mod.py"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        # Modify → working tree change
        f.write_text("x = 2\n")
        result = collect_changed_python_files(str(tmp_path))
        assert any("mod.py" in r for r in result)

    def test_staged_changes_detected(self, tmp_path: Path) -> None:
        """Staged .py file is collected via --cached diff."""
        subprocess.run(
            ["git", "init"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        f = tmp_path / "mod.py"
        f.write_text("x = 1\n")
        subprocess.run(
            ["git", "add", "mod.py"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        # Stage a change
        f.write_text("x = 2\n")
        subprocess.run(
            ["git", "add", "mod.py"],
            cwd=str(tmp_path),
            capture_output=True,
            check=True,
        )
        result = collect_changed_python_files(str(tmp_path))
        assert any("mod.py" in r for r in result)


# ── run_symbol_gate: reporting branches ──────────────────────────────


class TestRunSymbolGateSkippedReasons:
    def test_no_symbols_prints_skipped_reasons(self, tmp_path: Path, capsys: object) -> None:
        """File with no functions → skipped_reasons printed (lines 193-195)."""
        src = tmp_path / "constants.py"
        src.write_text("X = 42\nY = 'hello'\n")
        cov = tmp_path / "coverage.json"
        cov.write_text(
            json.dumps(
                {
                    "meta": {"version": "7.0"},
                    "files": {},
                }
            )
        )
        code = run_symbol_gate(
            project_root=str(tmp_path),
            coverage_json=str(cov),
            base=None,
            head=None,
            explicit_files=[str(src)],
            surface="mcp",
        )
        assert code == 0
        captured = capsys.readouterr()  # type: ignore[union-attr]
        assert "symbol gate notes:" in captured.out
        assert "No symbols targeted" in captured.out


class TestRunSymbolGateUncovered:
    def test_uncovered_symbols_printed(self, tmp_path: Path, capsys: object) -> None:
        """File with function but no coverage → uncovered printed (lines 198-201)."""
        src = tmp_path / "mod.py"
        src.write_text("def greet():\n    return 'hi'\n")
        cov = tmp_path / "coverage.json"
        cov.write_text(
            json.dumps(
                {
                    "meta": {"version": "7.0"},
                    "files": {
                        "other.py": {
                            "executed_lines": [1],
                            "missing_lines": [],
                            "excluded_lines": [],
                            "summary": {},
                        },
                    },
                }
            )
        )
        code = run_symbol_gate(
            project_root=str(tmp_path),
            coverage_json=str(cov),
            base=None,
            head=None,
            explicit_files=[str(src)],
            surface="mcp",
        )
        assert code == 1
        captured = capsys.readouterr()  # type: ignore[union-attr]
        assert "uncovered symbols:" in captured.out
        assert "greet" in captured.out

    def test_overflow_printed_when_over_25(self, tmp_path: Path, capsys: object) -> None:
        """More than 25 uncovered symbols → overflow message (lines 202-203)."""
        funcs = "\n".join(f"def func_{i}():\n    return {i}\n" for i in range(30))
        src = tmp_path / "many.py"
        src.write_text(funcs)
        cov = tmp_path / "coverage.json"
        # Need at least one valid file entry so parse_coverage_json succeeds
        cov.write_text(
            json.dumps(
                {
                    "meta": {"version": "7.0"},
                    "files": {
                        "unrelated.py": {
                            "executed_lines": [1],
                            "missing_lines": [],
                            "excluded_lines": [],
                            "summary": {},
                        },
                    },
                }
            )
        )
        code = run_symbol_gate(
            project_root=str(tmp_path),
            coverage_json=str(cov),
            base=None,
            head=None,
            explicit_files=[str(src)],
            surface="mcp",
        )
        assert code == 1
        captured = capsys.readouterr()  # type: ignore[union-attr]
        assert "... and " in captured.out
        assert " more" in captured.out


class TestRunSymbolGateUnresolved:
    def test_unresolved_required_printed(self, tmp_path: Path, capsys: object) -> None:
        """Unresolvable required_symbols → unresolved printed (line 206)."""
        src = tmp_path / "mod.py"
        src.write_text("def greet():\n    return 'hi'\n")
        cov = tmp_path / "coverage.json"
        cov.write_text(
            json.dumps(
                {
                    "meta": {"version": "7.0"},
                    "files": {
                        str(src): {
                            "executed_lines": [1, 2],
                            "missing_lines": [],
                            "excluded_lines": [],
                            "summary": {},
                        },
                    },
                }
            )
        )
        # Config with unresolvable required_symbols
        cfg_dir = tmp_path / ".claude"
        cfg_dir.mkdir()
        (cfg_dir / "lintgate.yaml").write_text(
            "controlplane:\n"
            "  channels:\n"
            "    tests:\n"
            "      symbol_coverage:\n"
            "        enabled: true\n"
            "        mode: changed\n"
            "        required_symbols:\n"
            "          - nonexistent.py::missing_func\n"
        )
        code = run_symbol_gate(
            project_root=str(tmp_path),
            coverage_json=str(cov),
            base=None,
            head=None,
            explicit_files=[str(src)],
            surface="mcp",
        )
        assert code == 1
        captured = capsys.readouterr()  # type: ignore[union-attr]
        assert "unresolved required symbols:" in captured.out
        assert "nonexistent.py::missing_func" in captured.out

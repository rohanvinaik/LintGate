#!/usr/bin/env python3
"""Fail-fast symbol coverage precheck for changed source files.

This runs before the full pre-push pytest suite to catch missing test coverage
early and avoid slow fix/push/fail loops.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


def _load_lintgate_helpers():
    """Resolve local imports when script is executed by file path."""
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from lintgate.channels.test_channel import find_impacted_tests
    from lintgate.symbol_gate_runner import collect_changed_python_files, run_symbol_gate

    return find_impacted_tests, collect_changed_python_files, run_symbol_gate


def _is_source_file(path: str) -> bool:
    p = Path(path)
    if p.suffix != ".py":
        return False
    if p.name == "conftest.py" or p.name.startswith("test_"):
        return False
    return "tests" not in p.parts and "test" not in p.parts


def _is_test_file(path: str) -> bool:
    p = Path(path)
    if p.suffix != ".py":
        return False
    if p.name == "conftest.py":
        return True
    return p.name.startswith("test_") or "tests" in p.parts or "test" in p.parts


def _head_has_parent(project_root: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD~1"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def run_precheck(project_root: str) -> int:
    find_impacted_tests, collect_changed_python_files, run_symbol_gate = _load_lintgate_helpers()
    base = "HEAD~1" if _head_has_parent(project_root) else None
    head = "HEAD" if base else None

    changed_py = collect_changed_python_files(
        project_root,
        base=base,
        head=head,
    )
    changed_sources = sorted(path for path in changed_py if _is_source_file(path))

    if not changed_sources:
        print("[lintgate] fast precheck: no changed source Python files")
        return 0

    changed_tests = [path for path in changed_py if _is_test_file(path)]
    impacted_tests = sorted(set(find_impacted_tests(changed_sources, project_root) + changed_tests))
    if not impacted_tests:
        print("[lintgate] fast precheck: no impacted tests found for changed source files")
        print("[lintgate] add/modify tests, or add a symbol_coverage waiver in .claude/lintgate.yaml")
        for src in changed_sources[:25]:
            print(f"  - {src}")
        return 1

    with tempfile.TemporaryDirectory(prefix="lintgate_fast_cov_") as tmp:
        coverage_json = str(Path(tmp) / "coverage.fast.json")
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            *impacted_tests,
            "--cov",
            "--cov-config=.coveragerc",
            "--cov-fail-under=0",
            f"--cov-report=json:{coverage_json}",
            "--tb=short",
            "-q",
        ]
        print(f"[lintgate] fast precheck: running {len(impacted_tests)} impacted test file(s)")
        result = subprocess.run(cmd, cwd=project_root)
        if result.returncode != 0:
            return result.returncode

        # Check only changed source files against the targeted coverage run.
        return run_symbol_gate(
            project_root=project_root,
            coverage_json=coverage_json,
            base=base,
            head=head,
            explicit_files=None,
            surface="prepush_fast",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run fast symbol-coverage precheck.")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args(argv)
    root = str(Path(args.project_root).resolve())
    return run_precheck(root)


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line runner for symbol-level coverage enforcement.

This wrapper is shared by CI workflows and local pre-push hooks so the same
gate logic is applied everywhere.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any

from lintgate.channels.symbol_coverage import run_symbol_coverage_gate


def _to_bool(value: Any, default: bool = False) -> bool:
    """Parse permissive boolean inputs from YAML/config sources."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def load_symbol_coverage_settings(project_root: str) -> dict[str, Any]:
    """Load symbol_coverage settings from .claude/lintgate.yaml.

    Falls back to enforcement-safe defaults when config is missing or invalid.
    """
    settings: dict[str, Any] = {
        "enabled": True,
        "mode": "changed",
        "diff_base": "HEAD",
    }
    cfg_path = Path(project_root) / ".claude" / "lintgate.yaml"
    if not cfg_path.is_file():
        return settings

    try:
        import yaml  # type: ignore[import-untyped]

        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        cp = raw.get("controlplane", {})
        channels = cp.get("channels", {}) if isinstance(cp, dict) else {}
        tests_cfg = channels.get("tests", {}) if isinstance(channels, dict) else {}
        symbol_cfg = (
            tests_cfg.get("symbol_coverage", {}) if isinstance(tests_cfg, dict) else {}
        )
        if isinstance(symbol_cfg, dict):
            settings.update(symbol_cfg)
    except Exception:
        # Keep defaults on parse/import failures.
        pass

    settings["enabled"] = _to_bool(settings.get("enabled"), default=True)
    return settings


def _run_git_list(
    project_root: str, args: list[str], *, timeout: int = 10
) -> list[str] | None:
    """Run a git command and return stripped output lines on success."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return lines


def collect_changed_python_files(
    project_root: str,
    *,
    explicit_files: list[str] | None = None,
    base: str | None = None,
    head: str | None = None,
) -> list[str]:
    """Collect changed Python files as absolute paths.

    Resolution order:
    1. Explicit files (if provided)
    2. Git diff for base/head range (when provided)
    3. Working tree + staged changes vs HEAD
    4. Tracked Python files fallback
    """
    root = Path(project_root).resolve()

    def _normalize(paths: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in paths:
            if not item.endswith(".py"):
                continue
            p = Path(item)
            abs_path = p if p.is_absolute() else root / p
            resolved = str(abs_path.resolve())
            if resolved in seen:
                continue
            if Path(resolved).is_file():
                seen.add(resolved)
                out.append(resolved)
        return out

    if explicit_files:
        return _normalize(explicit_files)

    candidates: list[str] = []

    # When base/head are explicitly provided, a successful diff is authoritative.
    # An empty result means "no Python files changed" — not "scan everything."
    if base and head:
        ranged = _run_git_list(
            str(root), ["diff", "--name-only", base, head, "--", "*.py"]
        )
        if ranged is not None:
            return _normalize(ranged)
    elif base:
        ranged = _run_git_list(str(root), ["diff", "--name-only", base, "--", "*.py"])
        if ranged is not None:
            return _normalize(ranged)

    # No explicit base/head or git failed — try working tree + staged changes
    for args in (
        ["diff", "--name-only", "HEAD", "--", "*.py"],
        ["diff", "--name-only", "--cached", "--", "*.py"],
    ):
        found = _run_git_list(str(root), args)
        if found:
            candidates.extend(found)

    if not candidates:
        tracked = _run_git_list(str(root), ["ls-files", "*.py"]) or []
        candidates.extend(tracked)

    return _normalize(candidates)


def run_symbol_gate(
    *,
    project_root: str,
    coverage_json: str,
    base: str | None,
    head: str | None,
    explicit_files: list[str] | None,
    surface: str,
) -> int:
    """Run symbol coverage gate and return process-style exit code."""
    coverage_path = Path(coverage_json)
    if not coverage_path.is_absolute():
        coverage_path = (Path(project_root) / coverage_path).resolve()
    if not coverage_path.is_file():
        print(f"[lintgate] missing coverage JSON: {coverage_path}")
        return 1

    settings = load_symbol_coverage_settings(project_root)
    if not _to_bool(settings.get("enabled"), default=True):
        print("[lintgate] symbol coverage gate disabled by configuration")
        return 0

    changed_files = collect_changed_python_files(
        project_root,
        explicit_files=explicit_files,
        base=base,
        head=head,
    )

    result = run_symbol_coverage_gate(
        coverage_json_path=str(coverage_path),
        changed_files=changed_files,
        project_root=project_root,
        settings=settings,
        surface=surface,
    )

    uncovered = [r for r in result.symbol_results if not r.covered]
    print(
        "[lintgate] symbol gate summary:"
        f" targets={len(result.symbol_results)}"
        f" uncovered={len(uncovered)}"
        f" unresolved_required={len(result.unresolved_required)}"
        f" waivers={len(result.waivers_applied)}"
    )
    if result.skipped_reasons:
        print("[lintgate] symbol gate notes:")
        for reason in result.skipped_reasons:
            print(f"  - {reason}")

    if uncovered:
        print("[lintgate] uncovered symbols:")
        for sr in uncovered[:25]:
            lines = ", ".join(str(x) for x in sr.missing_lines[:10]) or "-"
            print(f"  - {sr.symbol.symbol_key} (missing lines: {lines})")
        if len(uncovered) > 25:
            print(f"  ... and {len(uncovered) - 25} more")

    if result.unresolved_required:
        print("[lintgate] unresolved required symbols:")
        for sym in result.unresolved_required:
            print(f"  - {sym}")

    return 0 if result.passed else 1


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run LintGate symbol-level coverage gate.",
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root directory (default: current working directory).",
    )
    parser.add_argument(
        "--coverage-json",
        default="coverage.json",
        help="Path to pytest-cov JSON report (default: coverage.json).",
    )
    parser.add_argument(
        "--base",
        default=None,
        help="Git base revision for changed-file diff.",
    )
    parser.add_argument(
        "--head",
        default=None,
        help="Git head revision for changed-file diff.",
    )
    parser.add_argument(
        "--changed-file",
        action="append",
        default=[],
        help="Explicit changed file path (repeatable).",
    )
    parser.add_argument(
        "--surface",
        default="ci",
        help="Surface label passed to symbol gate (default: ci).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    project_root = str(Path(args.project_root).resolve())
    explicit = args.changed_file if args.changed_file else None
    return run_symbol_gate(
        project_root=project_root,
        coverage_json=args.coverage_json,
        base=args.base,
        head=args.head,
        explicit_files=explicit,
        surface=args.surface,
    )


if __name__ == "__main__":
    raise SystemExit(main())

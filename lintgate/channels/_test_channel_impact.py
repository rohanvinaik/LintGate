"""Impact detection — find test files affected by source changes.

Extracted from test_channel.py to keep the main channel file under 400 lines.
"""

from __future__ import annotations

from pathlib import Path


def _build_search_dirs(root: Path, src_path: Path) -> list[Path]:
    """Build the list of directories to search for test files."""
    search_dirs = [
        root / "tests",
        root / "test",
        src_path.parent,
        src_path.parent / "tests",
    ]
    try:
        rel = src_path.relative_to(root)
        if len(rel.parts) > 1:
            package_parts = rel.parts[:-1]
            if package_parts[0] in ("src", "lib", "lintgate"):
                package_parts = package_parts[1:]
            if package_parts:
                search_dirs.append(root / "tests" / Path(*package_parts))
    except ValueError:
        pass
    return search_dirs


def _find_joined_test(root: Path, src_path: Path) -> str | None:
    """Find underscore-joined test file (e.g. test_foo_bar.py)."""
    try:
        rel = src_path.relative_to(root)
        joined_name = (
            "test_"
            + "_".join(p for p in rel.with_suffix("").parts if p not in ("src", "lib", "__init__"))
            + ".py"
        )
        for test_dir in [root / "tests", root / "test"]:
            candidate = test_dir / joined_name
            if candidate.exists():
                return str(candidate)
    except ValueError:
        pass
    return None


def find_impacted_tests(changed_files: list[str], project_root: str) -> list[str]:
    """Find test files impacted by the changed source files.

    For each changed source file `src/foo/bar.py`, looks for:
    - tests/test_bar.py
    - tests/foo/test_bar.py
    - tests/test_foo_bar.py
    - test_bar.py (in same directory)
    """
    root = Path(project_root)
    impacted: list[str] = []
    seen: set[str] = set()

    for src_file in changed_files:
        src_path = Path(src_file)
        if src_path.suffix != ".py":
            continue

        basename = src_path.stem
        if basename.startswith("test_") or src_path.name == "conftest.py":
            if src_path.exists() and str(src_path) not in seen:
                impacted.append(str(src_path))
                seen.add(str(src_path))
            continue

        test_name = f"test_{basename}.py"
        for search_dir in _build_search_dirs(root, src_path):
            candidate = search_dir / test_name
            if candidate.exists() and str(candidate) not in seen:
                impacted.append(str(candidate))
                seen.add(str(candidate))

        joined = _find_joined_test(root, src_path)
        if joined and joined not in seen:
            impacted.append(joined)
            seen.add(joined)

    return sorted(impacted)

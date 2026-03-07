"""Phase 2: Grep-based static enforcement — no raw '::' key construction.

Scans lintgate/specification/, lintgate/channels/, and lintgate/linters/
for raw f-string key construction patterns like f"{...}::{...}" that
bypass canonical_function_key().
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Directories to scan for raw key construction
_SCAN_DIRS = [
    "lintgate/specification",
    "lintgate/channels",
    "lintgate/linters",
]

# Files that are allowed to use raw :: construction.
# - lintgate/keys.py: the canonical key module itself
# - composition.py: uses :: for edge keys (caller::callee), not function keys
# - symbol_coverage.py: uses :: for symbol span keys with POSIX path handling
# - dependency_clustering.py: uses :: for cluster target identifiers
_ALLOWED_FILES = {
    "lintgate/keys.py",
    "lintgate/specification/composition.py",
    "lintgate/channels/symbol_coverage.py",
    "lintgate/linters/structure_checks/dependency_clustering.py",
}

# Pattern: f"...{expr}::{expr}..." — raw key construction
_RAW_KEY_PATTERN = re.compile(r'f["\'].*\{[^}]+\}::\{[^}]+\}.*["\']')


def _find_raw_key_constructions() -> list[tuple[str, int, str]]:
    """Find f-string key constructions that bypass canonical_function_key.

    Returns list of (filepath, line_number, line_text) for violations.
    """
    violations: list[tuple[str, int, str]] = []
    project_root = Path(__file__).parent.parent

    for scan_dir in _SCAN_DIRS:
        dir_path = project_root / scan_dir
        if not dir_path.exists():
            continue

        for py_file in dir_path.rglob("*.py"):
            relpath = str(py_file.relative_to(project_root))
            if relpath in _ALLOWED_FILES:
                continue

            try:
                lines = py_file.read_text().splitlines()
            except OSError:
                continue

            for i, line in enumerate(lines, 1):
                # Skip comments and imports
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("import"):
                    continue

                if _RAW_KEY_PATTERN.search(line):
                    # Check if it's using canonical_function_key
                    if "canonical_function_key" in line:
                        continue
                    violations.append((relpath, i, stripped))

    return violations


def test_no_raw_key_construction():
    """No raw f'...{x}::{y}...' key construction in spec/channels/linters code.

    All key construction must go through canonical_function_key() from lintgate.keys.
    """
    violations = _find_raw_key_constructions()

    if violations:
        msg_parts = ["Raw key construction found (use canonical_function_key instead):"]
        for filepath, lineno, text in violations:
            msg_parts.append(f"  {filepath}:{lineno}: {text}")
        pytest.fail("\n".join(msg_parts))

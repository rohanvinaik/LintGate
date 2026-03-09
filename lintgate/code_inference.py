"""Code inference — derives compass claims from code artifacts.

Pure Python analysis that infers compass claims from pyproject.toml,
README, imports, directory structure, test patterns, commit messages,
and docstrings. All claims have provenance="inferred" and confidence
capped at 0.6.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path

from .compass import CompassClaim
from .discovery import should_skip_dir

# ── Constants ────────────────────────────────────────────────────────

_MAX_CONFIDENCE = 0.6
_MAX_PY_FILES = 50


_FRAMEWORK_MAP: dict[str, tuple[str, str]] = {
    "fastapi": ("Uses FastAPI for HTTP API layer", "solution"),
    "flask": ("Uses Flask for HTTP API layer", "solution"),
    "django": ("Uses Django web framework", "solution"),
    "sqlalchemy": ("Uses SQLAlchemy ORM", "solution"),
    "pydantic": ("Uses Pydantic for data validation", "implementation"),
    "pytest": ("Uses pytest for testing", "implementation"),
    "click": ("Uses Click for CLI", "solution"),
    "typer": ("Uses Typer for CLI", "solution"),
    "numpy": ("Uses NumPy for numerical computation", "solution"),
    "pandas": ("Uses pandas for data manipulation", "solution"),
    "torch": ("Uses PyTorch for ML", "solution"),
    "boto3": ("Uses AWS SDK (boto3)", "world"),
    "redis": ("Uses Redis", "world"),
}

_LAYER_MAP: dict[str, str] = {
    "controllers": "MVC controllers layer",
    "models": "Data models layer",
    "services": "Business logic services layer",
    "handlers": "Request handlers layer",
    "schemas": "Data validation schemas layer",
    "routers": "API routing layer",
}

_BADGE_PATTERNS: list[tuple[str, str]] = [
    (r"(?:actions|workflow|ci)", "CI pipeline detected"),
    (r"(?:codecov|coveralls|coverage)", "Code coverage tracking"),
    (r"(?:pypi\.org|pypi)", "Published to PyPI"),
]


# ── Helpers ──────────────────────────────────────────────────────────


def _claim(
    text: str,
    source: str,
    *,
    confidence: float = 0.5,
    origin_facet: str = "",
) -> CompassClaim:
    """Build an inferred claim with confidence capped at _MAX_CONFIDENCE."""
    return CompassClaim(
        text=text,
        source=source,
        confidence=min(confidence, _MAX_CONFIDENCE),
        provenance="inferred",
        origin_facet=origin_facet,
    )


def _collect_py_files(project_root: str) -> list[Path]:
    """Collect up to _MAX_PY_FILES .py files, skipping excluded dirs."""
    from .discovery import discover_project_files

    return [Path(f) for f in discover_project_files(project_root, limit=_MAX_PY_FILES)]


def _read_text_safe(path: Path) -> str:
    """Read file text, returning empty string on error."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ── Inference Sources ────────────────────────────────────────────────


def _infer_from_pyproject(project_root: str) -> list[CompassClaim]:
    """Parse pyproject.toml for project metadata."""
    text = _read_text_safe(Path(project_root) / "pyproject.toml")
    if not text:
        return []

    claims: list[CompassClaim] = []
    desc = re.search(r'^description\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if desc:
        claims.append(
            _claim(
                f"Project purpose: {desc.group(1)}",
                "pyproject.toml",
                confidence=0.5,
                origin_facet="core_theory",
            )
        )

    py_req = re.search(r'^requires-python\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if py_req:
        claims.append(_claim(f"Python version: {py_req.group(1)}", "pyproject.toml"))

    if re.search(r"\[tool\.ruff\]", text):
        claims.append(
            _claim(
                "Ruff linter configured",
                "pyproject.toml",
                origin_facet="enforceable_rules",
            )
        )
    if re.search(r"\[tool\.mypy\]", text):
        claims.append(
            _claim(
                "mypy type checking configured",
                "pyproject.toml",
                origin_facet="enforceable_rules",
            )
        )
    if re.search(r"\[tool\.pytest", text):
        claims.append(
            _claim(
                "pytest configured in pyproject.toml",
                "pyproject.toml",
                origin_facet="enforceable_rules",
            )
        )
    return claims


def _extract_first_paragraph(lines: list[str]) -> str:
    """Extract first content paragraph from markdown lines."""
    paragraph: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith(("#", "![", "[![", "<!--")):
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    text = " ".join(paragraph)
    return text[:200] if text else ""


def _infer_from_readme(project_root: str) -> list[CompassClaim]:
    """Extract claims from README first paragraph and badges."""
    for name in ("README.md", "README.rst", "README"):
        text = _read_text_safe(Path(project_root) / name)
        if text:
            break
    else:
        return []

    claims: list[CompassClaim] = []
    lines = text.split("\n")

    para = _extract_first_paragraph(lines)
    if para:
        claims.append(_claim(f"README: {para}", name, confidence=0.5, origin_facet="core_theory"))

    for pattern, badge_claim in _BADGE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            claims.append(_claim(badge_claim, name, confidence=0.4))

    return claims


def _infer_from_imports(project_root: str) -> list[CompassClaim]:
    """Detect frameworks from import statements."""
    import_set: set[str] = set()
    for py_file in _collect_py_files(project_root):
        try:
            tree = ast.parse(_read_text_safe(py_file), filename=str(py_file))
        except (SyntaxError, ValueError):
            continue
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    import_set.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                import_set.add(node.module.split(".")[0])

    return [
        _claim(claim_text, "imports", confidence=0.55, origin_facet="architecture")
        for lib, (claim_text, _) in _FRAMEWORK_MAP.items()
        if lib in import_set
    ]


def _infer_from_directory_structure(project_root: str) -> list[CompassClaim]:
    """Infer claims from directory layout."""
    root = Path(project_root)
    claims: list[CompassClaim] = []

    if (root / "src").is_dir():
        claims.append(
            _claim("Uses src/ layout", "directory_structure", origin_facet="abstractions")
        )

    for test_dir in ("tests", "test"):
        if (root / test_dir).is_dir():
            claims.append(
                _claim(
                    f"Tests in {test_dir}/",
                    "directory_structure",
                    origin_facet="enforceable_rules",
                )
            )
            break

    if (root / "docs").is_dir():
        claims.append(_claim("Has docs/ directory", "directory_structure", confidence=0.4))

    for entry in root.iterdir():
        if entry.is_dir() and entry.name in _LAYER_MAP:
            claims.append(
                _claim(
                    _LAYER_MAP[entry.name],
                    "directory_structure",
                    confidence=0.45,
                    origin_facet="architecture",
                )
            )

    return claims


def _scan_test_dir(test_dir: Path) -> dict[str, bool | int]:
    """Scan a test directory for framework signals. Returns detection flags."""
    flags: dict[str, bool | int] = {
        "conftest": False,
        "pytest": False,
        "unittest": False,
        "fixtures": False,
        "count": 0,
    }
    for dirpath, dirnames, filenames in os.walk(test_dir):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            if fname == "conftest.py":
                flags["conftest"] = True
            if fname.startswith("test_") or fname.endswith("_test.py"):
                flags["count"] = int(flags["count"]) + 1
            source = _read_text_safe(Path(dirpath) / fname)
            if "import pytest" in source or "from pytest" in source:
                flags["pytest"] = True
            if "import unittest" in source:
                flags["unittest"] = True
            if "@pytest.fixture" in source:
                flags["fixtures"] = True
    return flags


def _infer_from_test_patterns(project_root: str) -> list[CompassClaim]:
    """Detect testing framework and patterns."""
    root = Path(project_root)
    merged: dict[str, bool | int] = {
        "conftest": False,
        "pytest": False,
        "unittest": False,
        "fixtures": False,
        "count": 0,
    }
    for name in ("tests", "test"):
        if (root / name).is_dir():
            flags = _scan_test_dir(root / name)
            for k in ("conftest", "pytest", "unittest", "fixtures"):
                merged[k] = merged[k] or flags[k]
            merged["count"] = int(merged["count"]) + int(flags["count"])

    claims: list[CompassClaim] = []
    facet = "enforceable_rules"
    if merged["pytest"]:
        claims.append(_claim("Uses pytest", "test_patterns", confidence=0.55, origin_facet=facet))
    elif merged["unittest"]:
        claims.append(_claim("Uses unittest", "test_patterns", confidence=0.55, origin_facet=facet))
    if merged["conftest"]:
        claims.append(
            _claim(
                "Uses conftest.py for shared fixtures",
                "test_patterns",
                origin_facet=facet,
            )
        )
    if merged["fixtures"]:
        claims.append(_claim("Uses pytest fixtures", "test_patterns", origin_facet=facet))
    count = int(merged["count"])
    if count > 0:
        claims.append(_claim(f"Test suite: {count} test file(s)", "test_patterns"))
    return claims


def _infer_from_commit_messages(project_root: str) -> list[CompassClaim]:
    """Analyze recent git commits. Only subprocess call; fail-safe."""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-50"],
            capture_output=True,
            text=True,
            cwd=project_root,
            timeout=10,
        )
        if result.returncode != 0:
            return []
    except (OSError, subprocess.TimeoutExpired):
        return []

    lines = [ln.strip() for ln in result.stdout.strip().split("\n") if ln.strip()]
    if not lines:
        return []

    conv_count = 0
    for line in lines:
        parts = line.split(" ", 1)
        if len(parts) >= 2 and re.match(
            r"^(feat|fix|docs|style|refactor|test|chore|ci|perf|build)\b",
            parts[1],
        ):
            conv_count += 1

    if len(lines) > 0 and conv_count / len(lines) > 0.4:
        return [
            _claim(
                "Uses conventional commit format",
                "commits",
                origin_facet="enforceable_rules",
            )
        ]
    return []


def _extract_docstring_claims(py_files: list[Path]) -> list[CompassClaim]:
    """Extract first-line docstrings from modules and classes."""
    claims: list[CompassClaim] = []
    seen: set[str] = set()

    for py_file in py_files:
        try:
            tree = ast.parse(_read_text_safe(py_file), filename=str(py_file))
        except (SyntaxError, ValueError):
            continue

        module_doc = ast.get_docstring(tree)
        if module_doc:
            first = module_doc.split("\n")[0].strip()[:150]
            if len(first) > 15 and first not in seen:
                seen.add(first)
                claims.append(
                    _claim(
                        f"{py_file.name}: {first}",
                        f"docstring:{py_file.name}",
                        confidence=0.4,
                        origin_facet="core_theory",
                    )
                )

        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            class_doc = ast.get_docstring(node)
            if not class_doc:
                continue
            first = class_doc.split("\n")[0].strip()[:150]
            if len(first) > 15 and first not in seen:
                seen.add(first)
                claims.append(
                    _claim(
                        f"{py_file.name}:{node.name}: {first}",
                        f"docstring:{py_file.name}",
                        confidence=0.35,
                        origin_facet="abstractions",
                    )
                )

    return claims


def _infer_from_docstrings(project_root: str) -> list[CompassClaim]:
    """Extract claims from module and class docstrings."""
    py_files = _collect_py_files(project_root)
    return _extract_docstring_claims(py_files) if py_files else []


# ── Public API ───────────────────────────────────────────────────────


def infer_from_code(project_root: str) -> list[CompassClaim]:
    """Combine all inference sources and deduplicate by claim text."""
    sources = [
        _infer_from_pyproject,
        _infer_from_readme,
        _infer_from_imports,
        _infer_from_directory_structure,
        _infer_from_test_patterns,
        _infer_from_commit_messages,
        _infer_from_docstrings,
    ]

    all_claims: list[CompassClaim] = []
    for fn in sources:
        try:
            all_claims.extend(fn(project_root))
        except Exception:
            continue

    seen: set[str] = set()
    deduped: list[CompassClaim] = []
    for claim in all_claims:
        if claim.text not in seen:
            seen.add(claim.text)
            deduped.append(claim)
    return deduped

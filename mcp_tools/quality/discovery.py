"""Project discovery and layout detection helpers."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_SKIP_DIRS = frozenset(
    {
        ".venv",
        "venv",
        "env",
        ".git",
        "__pycache__",
        "node_modules",
        "build",
        "dist",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        ".nox",
    }
)


def _detect_python_version_fallback(root: Path) -> str | None:
    """Try to detect Python version from .python-version file."""
    pv_file = root / ".python-version"
    if not pv_file.exists():
        return None
    try:
        ver_match = re.search(r"(\d+\.\d+)", pv_file.read_text())
        return ver_match.group(1) if ver_match else None
    except OSError:
        return None


def _detect_license_fallback(root: Path) -> str | None:
    """Try to detect license from LICENSE file content."""
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE"):
        lic_path = root / name
        if not lic_path.exists():
            continue
        try:
            content = lic_path.read_text(errors="ignore")[:500]
        except OSError:
            break
        if "MIT" in content:
            return "MIT"
        if "Apache" in content:
            return "Apache-2.0"
        if "GNU GENERAL PUBLIC LICENSE" in content.upper():
            return "GPL-3.0"
        if "BSD" in content:
            return "BSD-3-Clause"
        break
    return None


def _scan_src_layout(src_entry: Path) -> list[str]:
    """Scan a src/ directory for Python packages (src-layout convention).

    Returns a list of paths like ``"src/pkg_name"`` for each sub-directory
    that contains an ``__init__.py``.
    """
    results: list[str] = []
    try:
        for sub in sorted(src_entry.iterdir()):
            if sub.is_dir() and (sub / "__init__.py").exists():
                results.append(f"src/{sub.name}")
    except OSError:
        pass
    return results


def _classify_entry(
    entry: Path,
    test_dirs: list[str],
    source_dirs: list[str],
    doc_dirs: list[str],
) -> None:
    """Classify a single directory entry and append to the appropriate list.

    Mutates *test_dirs*, *source_dirs*, and *doc_dirs* in place.
    """
    if entry.name in ("tests", "test"):
        if not test_dirs:
            test_dirs.append(entry.name)
    elif entry.name in ("docs", "doc"):
        doc_dirs.append(entry.name)
    elif (entry / "__init__.py").exists():
        source_dirs.append(entry.name)
    elif entry.name == "src":
        source_dirs.extend(_scan_src_layout(entry))


def _discover_subproject(
    entry: Path,
    max_depth: int,
    current_depth: int,
    source_dirs: list[str],
    test_dirs: list[str],
    doc_dirs: list[str],
) -> str | None:
    """Recurse into a subproject directory and collect its dirs.

    Returns a truncation reason if the recursive scan was truncated,
    otherwise ``None``.  Mutates *source_dirs*, *test_dirs*, and
    *doc_dirs* in place by prefixing discovered paths with the entry name.
    """
    if not ((entry / "pyproject.toml").exists() or (entry / "setup.py").exists()):
        return None

    sub_src, sub_test, sub_doc, sub_trunc = _scan_project_dirs(
        entry, [], max_depth=max_depth, current_depth=current_depth + 1
    )
    prefix = entry.name
    source_dirs.extend(f"{prefix}/{s}" for s in sub_src)
    test_dirs.extend(f"{prefix}/{t}" for t in sub_test)
    doc_dirs.extend(f"{prefix}/{d}" for d in sub_doc)
    return sub_trunc


def _scan_project_dirs(
    root: Path,
    test_dirs: list[str],
    max_depth: int = 5,
    current_depth: int = 0,
) -> tuple[list[str], list[str], list[str], str | None]:
    """Scan root for source, test, and doc directories.

    Returns (source_dirs, test_dirs, doc_dirs, truncation_reason).
    """
    if current_depth >= max_depth:
        return [], [], [], "max_depth_exceeded"

    try:
        entries = sorted(root.iterdir())
    except OSError:
        return [], [], [], None

    source_dirs: list[str] = []
    doc_dirs: list[str] = []
    truncation_reason: str | None = None
    can_recurse = current_depth < max_depth - 1

    for entry in entries:
        if not _is_scannable_dir(entry):
            continue

        _classify_entry(entry, test_dirs, source_dirs, doc_dirs)

        if can_recurse:
            sub_trunc = _discover_subproject(
                entry, max_depth, current_depth, source_dirs, test_dirs, doc_dirs
            )
            if sub_trunc:
                truncation_reason = sub_trunc

    return source_dirs, test_dirs, doc_dirs, truncation_reason


def _is_scannable_dir(entry: Path) -> bool:
    """Return True if *entry* is a non-hidden, non-skipped directory."""
    try:
        is_dir = entry.is_dir()
    except OSError:
        return False
    return is_dir and not entry.name.startswith(".") and entry.name not in _SKIP_DIRS


def _parse_pyproject_metadata(
    root: Path,
) -> tuple[str, str | None, list[str], bool]:
    """Extract python version, license, and test paths from pyproject.toml.

    Returns (python_version, license_id, test_dirs, has_pyproject).
    """
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return "3", None, [], False
    try:
        try:
            import tomllib  # type: ignore[import-not-found]
        except ModuleNotFoundError:
            import tomli as tomllib  # type: ignore[no-redef]
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return "3", None, [], True

    python_version = "3"
    requires_python = data.get("project", {}).get("requires-python", "")
    if requires_python:
        ver_match = re.search(r"(\d+\.\d+)", requires_python)
        if ver_match:
            python_version = ver_match.group(1)

    lic = data.get("project", {}).get("license", {})
    if isinstance(lic, dict):
        license_id = lic.get("text") or lic.get("file")
    elif isinstance(lic, str):
        license_id = lic
    else:
        license_id = None

    test_dirs = list(
        data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("testpaths", [])
    )
    return python_version, license_id, test_dirs, True


def _detect_project_layout(project_root: str, max_depth: int = 5) -> dict[str, Any]:
    """Detect source dirs, test dirs, Python version, and license."""
    root = Path(project_root)

    python_version, license_id, test_dirs, has_pyproject = _parse_pyproject_metadata(root)

    if python_version == "3":
        python_version = _detect_python_version_fallback(root) or "3"

    if not license_id:
        license_id = _detect_license_fallback(root)

    source_dirs, test_dirs, doc_dirs, truncation_reason = _scan_project_dirs(
        root, test_dirs, max_depth=max_depth
    )

    exclude_patterns = ["**/__pycache__/", "*.egg-info/"]
    for d in test_dirs + doc_dirs:
        exclude_patterns.append(f"{d}/")
    exclude_patterns.append(".claude/")

    result = {
        "source_dirs": source_dirs or ["."],
        "test_dirs": test_dirs,
        "doc_dirs": doc_dirs,
        "python_version": python_version,
        "license": license_id,
        "has_pyproject_toml": has_pyproject,
        "exclude_patterns": exclude_patterns,
    }
    if truncation_reason:
        result["scope_provenance"] = {"truncation_reason": truncation_reason}

    return result


def _detect_sonar_scanner() -> str | None:
    """Find pysonar-scanner or sonar-scanner executable."""
    import shutil

    for name in ("pysonar-scanner", "sonar-scanner"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _run_sonar_scanner(
    project_root: str,
    sonar_token: str,
    scanner_path: str,
) -> dict[str, Any]:
    """Execute sonar-scanner to push results to SonarCloud."""
    import subprocess

    cmd = [scanner_path]
    if "pysonar-scanner" in scanner_path:
        cmd.extend(
            [
                f"-Dproject.home={project_root}",
                "-read.project.config",
            ]
        )
    else:
        cmd.extend(
            [
                f"-Dsonar.projectBaseDir={project_root}",
            ]
        )

    env = os.environ.copy()
    env["SONAR_TOKEN"] = sonar_token

    try:
        result = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )

        success = result.returncode == 0
        # Extract key info from output
        output_text = result.stdout + result.stderr
        analysis_url = None
        for line in output_text.splitlines():
            if "ANALYSIS SUCCESSFUL" in line or "task?id=" in line:
                success = True
            if "ceTaskUrl" in line or "dashboard/index" in line:
                # Try to extract URL
                url_match = re.search(r"(https?://\S+)", line)
                if url_match:
                    analysis_url = url_match.group(1)

        return {
            "status": "success" if success else "failed",
            "exit_code": result.returncode,
            "scanner": scanner_path,
            "analysis_url": analysis_url,
            "output_tail": output_text[-500:] if output_text else "",
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "scanner": scanner_path}
    except (FileNotFoundError, OSError) as exc:
        return {"status": "error", "error": str(exc), "scanner": scanner_path}

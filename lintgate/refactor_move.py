"""Safe module refactoring engine — libcst-based import rewriting.

Provides atomic module move operations with:
- AST-based import scanning (works without libcst)
- libcst-based import rewriting (requires libcst for auto-apply)
- Targeted string scanning for import-bearing call sites only
- Subprocess-based import smoke gate
- Optional shim generation for backward compatibility

Graceful degradation: without libcst, dry_run mode shows what needs
changing but cannot auto-apply. Returns ``{"degraded": true, ...}``.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Any

# ── Types ─────────────────────────────────────────────────────────


@dataclass
class ImportReference:
    """A single import reference that needs rewriting."""

    file: str
    line: int
    kind: str  # "import", "from_import", "string_ref"
    old_text: str
    new_text: str
    context: str = ""  # e.g., "mock.patch", "importlib.import_module"


@dataclass
class MoveResult:
    """Result of a module move operation."""

    source: str
    destination: str
    dry_run: bool
    degraded: bool = False
    references_found: list[ImportReference] = field(default_factory=list)
    references_rewritten: int = 0
    shim_generated: bool = False
    shim_path: str = ""
    smoke_test_passed: bool | None = None
    smoke_test_error: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "source": self.source,
            "destination": self.destination,
            "dry_run": self.dry_run,
        }
        if self.degraded:
            d["degraded"] = True
        d["references_found"] = len(self.references_found)
        if self.references_found:
            d["references"] = [
                {
                    "file": r.file,
                    "line": r.line,
                    "kind": r.kind,
                    "old_text": r.old_text,
                    "new_text": r.new_text,
                    **({"context": r.context} if r.context else {}),
                }
                for r in self.references_found
            ]
        if not self.dry_run:
            d["references_rewritten"] = self.references_rewritten
        if self.shim_generated:
            d["shim_generated"] = True
            d["shim_path"] = self.shim_path
        if self.smoke_test_passed is not None:
            d["smoke_test_passed"] = self.smoke_test_passed
        if self.smoke_test_error:
            d["smoke_test_error"] = self.smoke_test_error
        if self.errors:
            d["errors"] = self.errors
        if self.warnings:
            d["warnings"] = self.warnings
        return d


# ── Import scanning (AST-based, always available) ────────────────


# ── Import scanning ───────────────────────────────────────────────


def scan_all_imports(
    project_root: str,
    old_module: str,
) -> list[ImportReference]:
    """Scan all Python files for imports of old_module.

    Uses ast (stdlib) — always available. Finds:
    - ``import old_module``
    - ``from old_module import ...``
    - ``from old_module.sub import ...``
    - String references in import-bearing call sites
    """
    refs: list[ImportReference] = []
    old_parts = old_module.split(".")

    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [
            d
            for d in dirnames
            if not d.startswith(".") and d != "__pycache__" and d != "node_modules" and d != ".git"
        ]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            filepath = os.path.join(dirpath, fname)
            try:
                rel_path = os.path.relpath(filepath, project_root)
            except ValueError:
                continue
            file_refs = _scan_file_imports(filepath, rel_path, old_module, old_parts)
            refs.extend(file_refs)

    return refs


def _scan_file_imports(
    filepath: str,
    rel_path: str,
    old_module: str,
    _old_parts: list[str],
) -> list[ImportReference]:
    """Scan a single file for imports of old_module."""
    try:
        with open(filepath, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (OSError, SyntaxError):
        return []

    refs: list[ImportReference] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == old_module or alias.name.startswith(old_module + "."):
                    refs.append(
                        ImportReference(
                            file=rel_path,
                            line=node.lineno,
                            kind="import",
                            old_text=f"import {alias.name}",
                            new_text="",  # Filled in by _compute_rewrites
                        )
                    )

        elif isinstance(node, ast.ImportFrom):
            if node.module and (
                node.module == old_module or node.module.startswith(old_module + ".")
            ):
                names = ", ".join(a.name for a in node.names)
                refs.append(
                    ImportReference(
                        file=rel_path,
                        line=node.lineno,
                        kind="from_import",
                        old_text=f"from {node.module} import {names}",
                        new_text="",
                    )
                )

        # String references in import-bearing call sites
        elif isinstance(node, ast.Call):
            _scan_string_refs(node, rel_path, old_module, refs)

    return refs


# ── Known import-bearing call site patterns ──────────────────────


def _scan_string_refs(
    node: ast.Call,
    rel_path: str,
    old_module: str,
    refs: list[ImportReference],
) -> None:
    """Scan a Call node for string references to old_module in import-bearing sites."""
    call_name = _get_call_name(node)
    if not call_name:
        return

    # Determine if this is an import-bearing call
    context = ""
    if call_name == "patch" or (call_name == "patch" and _is_mock_patch(node)):
        context = "mock.patch"
    elif call_name == "object" and _is_patch_object(node):
        context = "mock.patch.object"
    elif call_name == "setattr" and _is_monkeypatch_setattr(node):
        context = "monkeypatch.setattr"
    elif call_name == "import_module":
        context = "importlib.import_module"

    if not context:
        return

    # Check string arguments
    for arg in node.args:
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
            continue
        val = arg.value
        if old_module in val:
            refs.append(
                ImportReference(
                    file=rel_path,
                    line=node.lineno,
                    kind="string_ref",
                    old_text=val,
                    new_text="",  # Filled in by _compute_rewrites
                    context=context,
                )
            )


def _get_call_name(node: ast.Call) -> str | None:
    """Get the leaf name of a call."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_mock_patch(node: ast.Call) -> bool:
    """Check if call is mock.patch(...)."""
    func = node.func
    if isinstance(func, ast.Name) and func.id == "patch":
        return True
    return isinstance(func, ast.Attribute) and func.attr == "patch"


def _is_patch_object(node: ast.Call) -> bool:
    """Check if call is patch.object(...)."""
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "object":
        val = func.value
        if isinstance(val, ast.Name) and val.id == "patch":
            return True
        if isinstance(val, ast.Attribute) and val.attr == "patch":
            return True
    return False


def _is_monkeypatch_setattr(node: ast.Call) -> bool:
    """Check if call is monkeypatch.setattr(...)."""
    func = node.func
    return isinstance(func, ast.Attribute) and func.attr == "setattr"


from .refactor_move_rewrite import (  # noqa: F401, E402
    _compute_rewrites,
    _cst_module_to_str,
    _extract_public_names,
    _has_libcst,
    _rewrite_file_with_libcst,
    _rewrite_import_bearing_strings,
    _str_to_cst_module,
    generate_shim,
    smoke_test_import,
)

# ── Main orchestration ───────────────────────────────────────────


# ── Orchestrator ──────────────────────────────────────────────────


def refactor_move(
    project_root: str,
    source: str,
    destination: str,
    *,
    dry_run: bool = True,
    generate_shim_file: bool = True,
) -> MoveResult:
    """Move a module from source to destination with import rewriting.

    Args:
        project_root: Absolute path to the project root.
        source: Dotted module path (e.g., ``"lintgate.old_module"``).
        destination: Dotted module path (e.g., ``"lintgate.new_module"``).
        dry_run: If True, only scan and report — don't rewrite.
        generate_shim_file: If True, generate a backward-compatibility shim.

    Returns:
        MoveResult with all findings and rewrite outcomes.
    """
    result = MoveResult(source=source, destination=destination, dry_run=dry_run)

    # Validate source exists
    source_path = os.path.join(project_root, source.replace(".", os.sep) + ".py")
    if not os.path.isfile(source_path):
        result.errors.append(f"Source module not found: {source_path}")
        return result

    # Scan for all references
    refs = scan_all_imports(project_root, source)
    _compute_rewrites(refs, source, destination)
    result.references_found = refs

    if dry_run:
        # In dry-run, check if we can auto-apply
        if not _has_libcst():
            result.degraded = True
            result.warnings.append(
                "libcst not installed — dry_run shows what needs changing "
                "but auto-apply requires: pip install 'lintgate[refactor]'"
            )
        return result

    # ── Apply phase ──────────────────────────────────────────────

    # Gate: libcst is required for auto-apply. Without it, refuse to
    # move the file — a moved file with unrewritten imports is worse
    # than no move at all.
    if not _has_libcst():
        result.degraded = True
        result.warnings.append(
            "libcst not installed — cannot auto-apply. "
            "Install with: pip install 'lintgate[refactor]'"
        )
        return result

    dest_path = os.path.join(project_root, destination.replace(".", os.sep) + ".py")

    # Move the file
    if source_path != dest_path:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        if os.path.exists(dest_path):
            result.warnings.append(f"Destination already exists: {dest_path}")
        else:
            try:
                os.rename(source_path, dest_path)
            except OSError as e:
                result.errors.append(f"Failed to move file: {e}")
                return result

    # Rewrite imports (libcst guaranteed available by gate above)
    rewritten = 0
    files_to_rewrite: set[str] = set()
    for ref in refs:
        full_path = os.path.join(project_root, ref.file)
        files_to_rewrite.add(full_path)

    for fpath in sorted(files_to_rewrite):
        success, error = _rewrite_file_with_libcst(fpath, source, destination)
        if success:
            rewritten += 1
        elif error:
            result.errors.append(f"Rewrite failed for {fpath}: {error}")

    # Rewrite string refs in import-bearing call sites only
    string_refs = [r for r in refs if r.kind == "string_ref"]
    if string_refs:
        str_rewritten = _rewrite_import_bearing_strings(
            project_root, string_refs, source, destination
        )
        rewritten += str_rewritten

    result.references_rewritten = rewritten

    # Generate shim if requested
    if generate_shim_file and source_path != dest_path:
        try:
            shim_path = generate_shim(project_root, source, destination)
            result.shim_generated = True
            result.shim_path = os.path.relpath(shim_path, project_root)
        except Exception as e:
            result.warnings.append(f"Shim generation failed: {e}")

    # Smoke test
    passed, error = smoke_test_import(destination, project_root)
    result.smoke_test_passed = passed
    result.smoke_test_error = error

    return result

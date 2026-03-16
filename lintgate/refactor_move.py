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
import subprocess
import sys
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


# ── Rewrite computation ──────────────────────────────────────────


# ── Import rewriting ──────────────────────────────────────────────


def _compute_rewrites(
    refs: list[ImportReference],
    old_module: str,
    new_module: str,
) -> None:
    """Fill in new_text for each reference."""
    for ref in refs:
        if ref.kind in ("import", "from_import", "string_ref"):
            ref.new_text = ref.old_text.replace(old_module, new_module)


# ── libcst-based rewriting ───────────────────────────────────────


def _has_libcst() -> bool:
    """Check if libcst is available."""
    try:
        import libcst as _libcst  # noqa: F401

        del _libcst
        return True
    except ImportError:
        return False


def _rewrite_file_with_libcst(
    filepath: str,
    old_module: str,
    new_module: str,
) -> tuple[bool, str]:
    """Rewrite imports in a file using libcst.

    Returns (success, error_message).
    """
    try:
        import libcst as cst
    except ImportError:
        return False, "libcst not available"

    try:
        with open(filepath, encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        return False, str(e)

    try:
        tree = cst.parse_module(source)
    except cst.ParserSyntaxError as e:
        return False, f"Parse error: {e}"

    class ImportRewriter(cst.CSTTransformer):
        def __init__(self) -> None:
            self.changed = False

        def leave_ImportFrom(  # noqa: N802
            self,
            original_node: cst.ImportFrom,
            updated_node: cst.ImportFrom,
        ) -> cst.ImportFrom:
            # Get the module string
            module_str = _cst_module_to_str(updated_node.module)
            if module_str and (module_str == old_module or module_str.startswith(old_module + ".")):
                new_mod_str = module_str.replace(old_module, new_module, 1)
                new_mod = _str_to_cst_module(new_mod_str)
                if new_mod is not None:
                    self.changed = True
                    return updated_node.with_changes(module=new_mod)
            return updated_node

        def leave_Import(  # noqa: N802
            self,
            original_node: cst.Import,
            updated_node: cst.Import,
        ) -> cst.Import:
            if not isinstance(updated_node.names, cst.ImportStar):
                new_names = []
                changed = False
                for alias in updated_node.names:
                    name_str = _cst_module_to_str(alias.name)
                    if name_str and (
                        name_str == old_module or name_str.startswith(old_module + ".")
                    ):
                        new_name_str = name_str.replace(old_module, new_module, 1)
                        new_name = _str_to_cst_module(new_name_str)
                        if new_name is not None:
                            new_names.append(alias.with_changes(name=new_name))
                            changed = True
                            continue
                    new_names.append(alias)
                if changed:
                    self.changed = True
                    return updated_node.with_changes(names=new_names)
            return updated_node

        # NOTE: String literals are NOT rewritten here. The CST transformer
        # only handles import/from-import statements. String refs in
        # import-bearing call sites (mock.patch, monkeypatch.setattr,
        # importlib.import_module) are rewritten separately via targeted
        # regex replacement in _rewrite_import_bearing_strings(), which
        # only touches strings inside those specific call patterns.

    rewriter = ImportRewriter()
    try:
        new_tree = tree.visit(rewriter)
    except Exception as e:
        return False, f"CST transform error: {e}"

    if not rewriter.changed:
        return True, ""  # No changes needed

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_tree.code)
    except OSError as e:
        return False, f"Write error: {e}"

    return True, ""


def _rewrite_import_bearing_strings(
    project_root: str,
    string_refs: list[ImportReference],
    _old_module: str,
    _new_module: str,
) -> int:
    """Rewrite string refs in import-bearing call sites only.

    Targets only the specific string literals identified by the AST scanner
    in _scan_string_refs (mock.patch, monkeypatch.setattr,
    importlib.import_module). Does NOT rewrite arbitrary strings.

    Uses line-targeted text replacement so only the exact string on the
    exact line reported by the scanner is touched.
    """
    rewritten = 0
    # Group refs by file
    by_file: dict[str, list[ImportReference]] = {}
    for ref in string_refs:
        full_path = os.path.join(project_root, ref.file)
        by_file.setdefault(full_path, []).append(ref)

    for fpath, refs_in_file in by_file.items():
        try:
            with open(fpath, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue

        changed = False
        for ref in refs_in_file:
            line_idx = ref.line - 1  # 0-indexed
            if 0 <= line_idx < len(lines):
                old_line = lines[line_idx]
                # Only replace the exact old_text within this line
                if ref.old_text in old_line:
                    lines[line_idx] = old_line.replace(ref.old_text, ref.new_text, 1)
                    changed = True

        if changed:
            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.writelines(lines)
                rewritten += 1
            except OSError:
                pass

    return rewritten


def _cst_module_to_str(module: Any) -> str | None:
    """Convert a libcst module attribute to a dotted string."""
    try:
        import libcst as cst
    except ImportError:
        return None
    if module is None:
        return None
    if isinstance(module, cst.Attribute):
        left = _cst_module_to_str(module.value)
        if left is None:
            return None
        return f"{left}.{module.attr.value}"
    if isinstance(module, cst.Name):
        return module.value
    return None


def _str_to_cst_module(dotted: str) -> Any:
    """Convert a dotted string to a libcst Attribute/Name chain."""
    try:
        import libcst as cst
    except ImportError:
        return None
    parts = dotted.split(".")
    if not parts:
        return None
    node: Any = cst.Name(parts[0])
    for part in parts[1:]:
        node = cst.Attribute(value=node, attr=cst.Name(part))
    return node


# ── Shim generation ──────────────────────────────────────────────


# ── Shim generation ───────────────────────────────────────────────


def generate_shim(
    project_root: str,
    old_module: str,
    new_module: str,
) -> str:
    """Generate a backward-compatibility shim at the old module location.

    The shim re-exports all public names from the new location so
    existing code that hasn't been updated yet still works.

    Returns the shim file path.
    """
    old_path = os.path.join(project_root, old_module.replace(".", os.sep) + ".py")

    # Discover public names from the new module
    new_path = os.path.join(project_root, new_module.replace(".", os.sep) + ".py")
    public_names = _extract_public_names(new_path)

    lines = [
        f'"""Backward-compatibility shim — module moved to {new_module}.',
        "",
        "This file re-exports all public names from the new location.",
        "Remove this shim once all dependents have been updated.",
        '"""',
        "",
        f"from {new_module} import *  # noqa: F401, F403",
        "",
    ]

    if public_names:
        names_str = ", ".join(f'"{n}"' for n in sorted(public_names))
        lines.insert(-1, f"__all__ = [{names_str}]")
        lines.insert(-1, "")

    os.makedirs(os.path.dirname(old_path), exist_ok=True)
    with open(old_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return old_path


def _extract_public_names(filepath: str) -> list[str]:
    """Extract public names from a Python file via AST."""
    try:
        with open(filepath, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError):
        return []

    # Check for __all__
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "__all__"
                    and isinstance(node.value, (ast.List, ast.Tuple))
                ):
                    return [
                        elt.value
                        for elt in node.value.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    ]

    # Fallback: all non-underscore top-level names
    names: list[str] = []
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.append(target.id)
    return names


# ── Smoke gate ───────────────────────────────────────────────────


# ── Smoke testing ─────────────────────────────────────────────────


def smoke_test_import(
    module_name: str,
    project_root: str,
    *,
    timeout: int = 10,
) -> tuple[bool, str]:
    """Smoke-test that a module can be imported.

    Uses subprocess to avoid polluting the current process.

    Returns (success, error_message).
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", f"import {module_name}"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return True, ""
        return False, result.stderr.strip()[:500]
    except subprocess.TimeoutExpired:
        return False, f"Import timed out after {timeout}s"
    except Exception as e:
        return False, str(e)


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

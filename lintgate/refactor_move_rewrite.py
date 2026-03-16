"""Import rewriting, shim generation, and smoke testing for refactor_move.

Split from refactor_move.py — contains libcst-based import rewriting,
import-bearing string rewriting, shim generation, smoke testing,
and CST conversion helpers.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .refactor_move import ImportReference

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


def _parse_cst_module(filepath: str) -> tuple[Any | None, str]:
    """Parse a file into a libcst module. Returns (tree, error)."""
    try:
        import libcst as cst
    except ImportError:
        return None, "libcst not available"
    try:
        with open(filepath, encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        return None, str(e)
    try:
        return cst.parse_module(source), ""
    except cst.ParserSyntaxError as e:
        return None, f"Parse error: {e}"


def _build_import_rewriter(old_module: str, new_module: str) -> Any:
    """Create a libcst CSTTransformer that rewrites imports from old to new module."""
    import libcst as cst

    class ImportRewriter(cst.CSTTransformer):
        def __init__(self) -> None:
            self.changed = False

        def leave_ImportFrom(  # noqa: N802
            self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
        ) -> cst.ImportFrom:
            module_str = _cst_module_to_str(updated_node.module)
            if module_str and (module_str == old_module or module_str.startswith(old_module + ".")):
                new_mod = _str_to_cst_module(module_str.replace(old_module, new_module, 1))
                if new_mod is not None:
                    self.changed = True
                    return updated_node.with_changes(module=new_mod)
            return updated_node

        def leave_Import(self, original_node: cst.Import, updated_node: cst.Import) -> cst.Import:  # noqa: N802
            if not isinstance(updated_node.names, cst.ImportStar):
                new_names = []
                changed = False
                for alias in updated_node.names:
                    name_str = _cst_module_to_str(alias.name)
                    if name_str and (
                        name_str == old_module or name_str.startswith(old_module + ".")
                    ):
                        new_name = _str_to_cst_module(name_str.replace(old_module, new_module, 1))
                        if new_name is not None:
                            new_names.append(alias.with_changes(name=new_name))
                            changed = True
                            continue
                    new_names.append(alias)
                if changed:
                    self.changed = True
                    return updated_node.with_changes(names=new_names)
            return updated_node

    return ImportRewriter()


def _rewrite_file_with_libcst(
    filepath: str,
    old_module: str,
    new_module: str,
) -> tuple[bool, str]:
    """Rewrite imports in a file using libcst. Returns (success, error_message)."""
    tree, error = _parse_cst_module(filepath)
    if tree is None:
        return False, error

    rewriter = _build_import_rewriter(old_module, new_module)
    try:
        new_tree = tree.visit(rewriter)
    except Exception as e:
        return False, f"CST transform error: {e}"

    if not rewriter.changed:
        return True, ""

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
    """Rewrite string refs in import-bearing call sites only."""
    rewritten = 0
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
            line_idx = ref.line - 1
            if 0 <= line_idx < len(lines):
                old_line = lines[line_idx]
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


# ── Shim generation ───────────────────────────────────────────────


def generate_shim(
    project_root: str,
    old_module: str,
    new_module: str,
) -> str:
    """Generate a backward-compatibility shim at the old module location."""
    old_path = os.path.join(project_root, old_module.replace(".", os.sep) + ".py")

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


# ── Smoke testing ─────────────────────────────────────────────────


def smoke_test_import(
    module_name: str,
    project_root: str,
    *,
    timeout: int = 10,
) -> tuple[bool, str]:
    """Smoke-test that a module can be imported."""
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

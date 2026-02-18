"""Architecture and boundary enforcement checker.

Tier 3 — runs on architectural changes. Pure Python stdlib (AST-based),
no external dependencies.

Inspired by three sources:
- import-linter (pypi): Declarative layer contracts that enforce which
  modules can import from which. We reimplement the core concept as a
  lightweight AST check rather than requiring a separate tool install.
- ARC_AGI_3's stage boundary enforcement: Semgrep rules preventing
  cassette stages from importing each other. We generalize this into
  configurable layer contracts.
- TailChasingFixer's TAD (Topologically Associating Domains) detector:
  Identifies clusters of tightly-coupled functions. We adapt this as
  a "responsibility diffusion" check on module-level exports.

Configuration via lintgate.yaml:
    architecture:
      layers:
        - name: "presentation"
          modules: ["app.views", "app.templates", "app.api"]
          may_import: ["app.services", "app.models"]
          must_not_import: ["app.db", "app.migrations"]
        - name: "services"
          modules: ["app.services"]
          may_import: ["app.models", "app.utils"]
          must_not_import: ["app.views", "app.api"]
      max_module_exports: 15
      max_circular_depth: 2
"""

from __future__ import annotations

import ast
import os
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable


class ArchitectureChecker(BaseLinter):
    """Architecture boundary enforcer — catches layer violations.

    Checks:
    1. Layer contract violations (module A imports forbidden module B)
    2. Circular import detection (A→B→A cycles)
    3. Module responsibility diffusion (too many unrelated public exports)

    All checks are AST-based — no execution, no external tools.
    """

    name = "architecture_checker"
    tier = 3
    timeout_ms = 5000
    required_tool = None  # Pure AST, always available

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        """Run architecture checks on specified files."""
        layers = ctx.config.get("layers", [])
        max_exports = ctx.config.get("max_module_exports", 15)

        # Layer contract enforcement (only if layers are configured)
        if layers:
            yield from self._check_layer_contracts(ctx.files, layers, ctx)

        # Circular import detection
        yield from self._check_circular_imports(ctx.files, ctx)

        # Module responsibility diffusion
        yield from self._check_module_exports(ctx.files, max_exports)

    # ─── Layer contract enforcement ──────────────────────────────────

    def _check_layer_contracts(
        self,
        files: list[str],
        layers: list[dict[str, Any]],
        ctx: LinterContext,
    ) -> Iterable[LintIssue]:
        """Verify import statements respect declared layer boundaries."""
        layer_map = _build_layer_map(layers)

        for filepath in files:
            module_name = _filepath_to_module(filepath, ctx.project_root)
            if not module_name:
                continue

            file_layer = _find_layer(module_name, layer_map)
            if not file_layer:
                continue

            yield from _check_file_layer_imports(
                filepath, module_name, file_layer, layer_map,
            )

    # ─── Circular import detection ───────────────────────────────────

    def _check_circular_imports(
        self,
        files: list[str],
        ctx: LinterContext,
    ) -> Iterable[LintIssue]:
        """Detect circular import chains among the linted files.

        Builds a directed graph of imports between project files, then
        detects cycles. Only reports cycles involving files being linted.
        """
        # Build import graph (module → set of imported modules)
        graph: dict[str, set[str]] = defaultdict(set)
        file_map: dict[str, str] = {}  # module → filepath

        for filepath in files:
            module = _filepath_to_module(filepath, ctx.project_root)
            if module:
                file_map[module] = filepath
                imports = _extract_imports(filepath)
                for imp_module, _ in imports:
                    # Only track project-internal imports
                    if _is_project_local(imp_module, ctx.project_root):
                        graph[module].add(imp_module)

        # Find cycles using DFS
        cycles = _find_cycles(graph)

        # Report each unique cycle
        seen_cycles: set[frozenset[str]] = set()
        for cycle in cycles:
            cycle_key = frozenset(cycle)
            if cycle_key in seen_cycles:
                continue
            seen_cycles.add(cycle_key)

            # Only report if at least one member is in our file set
            relevant_files = [m for m in cycle if m in file_map]
            if not relevant_files:
                continue

            cycle_str = " → ".join(cycle + [cycle[0]])
            filepath = file_map.get(relevant_files[0], "")

            yield LintIssue(
                linter="architecture",
                kind="circular-import",
                message=f"Circular import detected: {cycle_str}",
                file=filepath,
                severity="warning",
                confidence=0.9,  # Some circular imports are intentional (lazy imports)
                evidence={"cycle": cycle, "length": len(cycle)},
                suggestions=[
                    "Break the cycle by moving shared types to a common module",
                    "Use lazy imports (import inside function) if the cycle is unavoidable",
                    "Consider dependency injection to decouple the modules",
                ],
            )

    # ─── Module responsibility diffusion ─────────────────────────────

    def _check_module_exports(
        self,
        files: list[str],
        max_exports: int,
    ) -> Iterable[LintIssue]:
        """Check if modules export too many unrelated public names.

        Adapted from TailChasingFixer's responsibility diffusion concept:
        a module with many unrelated exports is doing too many things.
        """
        for filepath in files:
            exports = _count_public_exports(filepath)
            if exports > max_exports:
                yield LintIssue(
                    linter="architecture",
                    kind="too-many-exports",
                    message=(
                        f"Module has {exports} public exports (limit: {max_exports}). "
                        f"This suggests mixed responsibilities."
                    ),
                    file=filepath,
                    severity="informational",
                    confidence=0.8,
                    evidence={"count": exports, "threshold": max_exports},
                    suggestions=[
                        "Split into focused sub-modules",
                        "Use __all__ to explicitly define the public API",
                        "Consider whether all exports serve the same purpose",
                    ],
                )


# ─── Import extraction helpers ───────────────────────────────────────────


def _extract_imports(filepath: str) -> list[tuple[str, int]]:
    """Extract all import module names and line numbers from a file.

    Returns list of (module_name, lineno) tuples.
    """
    try:
        with open(filepath) as f:
            source = f.read()
    except OSError:
        return []

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []

    imports: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            # Skip relative imports
            imports.append((node.module, node.lineno))

    return imports


def _filepath_to_module(filepath: str, project_root: str) -> str | None:
    """Convert a file path to a dotted module name.

    /project/src/app/views.py → app.views
    /project/app/models/__init__.py → app.models
    """
    try:
        relpath = os.path.relpath(filepath, project_root)
    except ValueError:
        return None

    # Normalize path separators
    relpath = relpath.replace(os.sep, "/")

    # Strip common prefixes
    for prefix in ("src/", "lib/"):
        if relpath.startswith(prefix):
            relpath = relpath[len(prefix):]
            break

    # Convert to module path
    if relpath.endswith("/__init__.py"):
        module = relpath[:-len("/__init__.py")]
    elif relpath.endswith(".py"):
        module = relpath[:-3]
    else:
        return None

    return module.replace("/", ".")


def _is_project_local(module_name: str, project_root: str) -> bool:
    """Check if a module name corresponds to a local project file."""
    top_level = module_name.split(".")[0]
    candidates = [
        os.path.join(project_root, top_level + ".py"),
        os.path.join(project_root, top_level, "__init__.py"),
        os.path.join(project_root, "src", top_level + ".py"),
        os.path.join(project_root, "src", top_level, "__init__.py"),
    ]
    return any(os.path.exists(c) for c in candidates)


# ─── Layer map helpers ───────────────────────────────────────────────────


def _build_layer_map(
    layers: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build lookup from module prefix → layer config."""
    layer_map: dict[str, dict[str, Any]] = {}
    for layer in layers:
        for module in layer.get("modules", []):
            layer_map[module] = layer
    return layer_map


def _find_layer(
    module_name: str, layer_map: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Find which layer a module belongs to (longest prefix match)."""
    best_match: dict[str, Any] | None = None
    best_length = 0

    for prefix, layer in layer_map.items():
        if (
            module_name == prefix or module_name.startswith(prefix + ".")
        ) and len(prefix) > best_length:
            best_match = layer
            best_length = len(prefix)

    return best_match


# ─── Cycle detection ─────────────────────────────────────────────────────


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Find all cycles in a directed graph using DFS.

    Returns list of cycles, each cycle is a list of nodes.
    Only finds cycles up to length 5 (deeper cycles are unusual).
    """
    cycles: list[list[str]] = []
    visited: set[str] = set()
    path: list[str] = []
    path_set: set[str] = set()

    def dfs(node: str) -> None:
        if len(path) > 5:  # Cap cycle depth
            return
        if node in path_set:
            # Found a cycle — extract it
            cycle_start = path.index(node)
            cycles.append(path[cycle_start:])
            return
        if node in visited:
            return

        path.append(node)
        path_set.add(node)

        for neighbor in graph.get(node, set()):
            dfs(neighbor)

        path.pop()
        path_set.discard(node)
        visited.add(node)

    for node in graph:
        if node not in visited:
            dfs(node)

    return cycles


# ─── Export counting ─────────────────────────────────────────────────────


def _check_file_layer_imports(
    filepath: str,
    module_name: str,
    file_layer: dict[str, Any],
    layer_map: dict[str, dict[str, Any]],
) -> Iterable[LintIssue]:
    """Check a single file's imports against its layer's contract."""
    imports = _extract_imports(filepath)
    must_not_import = set(file_layer.get("must_not_import", []))
    may_import = set(file_layer.get("may_import", []))
    layer_name = file_layer["name"]

    for imp_module, lineno in imports:
        yield from _check_forbidden_import(
            filepath, module_name, layer_name, imp_module, lineno, must_not_import,
        )
        if may_import:
            yield from _check_allowlist_import(
                filepath, module_name, layer_name, imp_module, lineno,
                may_import, file_layer, layer_map,
            )


def _check_forbidden_import(
    filepath: str, module_name: str, layer_name: str,
    imp_module: str, lineno: int, must_not_import: set[str],
) -> Iterable[LintIssue]:
    """Check if an import violates a must_not_import rule."""
    for forbidden in must_not_import:
        if _module_matches_prefix(imp_module, forbidden):
            yield LintIssue(
                linter="architecture",
                kind="layer-violation",
                message=(
                    f"Layer violation: '{module_name}' "
                    f"(layer '{layer_name}') "
                    f"imports forbidden module '{imp_module}'"
                ),
                file=filepath,
                line=lineno,
                severity="blocking",
                confidence=1.0,
                evidence={
                    "layer": layer_name,
                    "imported": imp_module,
                    "forbidden_prefix": forbidden,
                },
                suggestions=[
                    f"Layer '{layer_name}' must not import from '{forbidden}'",
                    "Move the dependency to an allowed layer or use dependency injection",
                ],
            )


def _check_allowlist_import(
    filepath: str, module_name: str, layer_name: str,
    imp_module: str, lineno: int, may_import: set[str],
    file_layer: dict[str, Any],
    layer_map: dict[str, dict[str, Any]],
) -> Iterable[LintIssue]:
    """Check if a cross-layer import is in the allowed list."""
    imp_layer = _find_layer(imp_module, layer_map)
    if not imp_layer or imp_layer["name"] == file_layer["name"]:
        return

    allowed = any(
        _module_matches_prefix(imp_module, prefix)
        for prefix in may_import
    )
    if not allowed:
        yield LintIssue(
            linter="architecture",
            kind="layer-violation",
            message=(
                f"Layer violation: '{module_name}' "
                f"(layer '{layer_name}') "
                f"imports '{imp_module}' which is not in allowed list"
            ),
            file=filepath,
            line=lineno,
            severity="warning",
            confidence=0.85,
            evidence={
                "layer": layer_name,
                "imported": imp_module,
                "allowed": list(may_import),
            },
            suggestions=[
                f"Add '{imp_module.split('.')[0]}' to the may_import list, "
                f"or restructure to avoid cross-layer dependency",
            ],
        )


def _module_matches_prefix(module: str, prefix: str) -> bool:
    """Check if a module matches a prefix (exact or dotted child)."""
    return module == prefix or module.startswith(prefix + ".")


def _count_public_exports(filepath: str) -> int:
    """Count public (non-underscore) top-level definitions in a module."""
    try:
        with open(filepath) as f:
            source = f.read()
    except OSError:
        return 0

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return 0

    # Check for explicit __all__ first
    all_count = _get_all_count(tree)
    if all_count is not None:
        return all_count

    # No __all__ — count public top-level names
    return _count_public_names(tree)


def _get_all_count(tree: ast.Module) -> int | None:
    """Get count from __all__ if defined, or None."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Name)
                and target.id == "__all__"
                and isinstance(node.value, (ast.List, ast.Tuple))
            ):
                return len(node.value.elts)
    return None


def _count_public_names(tree: ast.Module) -> int:
    """Count public top-level names (functions, classes, variables)."""
    count = 0
    for node in tree.body:
        name = _get_public_name(node)
        if name and not name.startswith("_"):
            count += 1
    return count


def _get_public_name(node: ast.stmt) -> str | None:
    """Extract the public name from a top-level statement, if any."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name
    if isinstance(node, ast.ClassDef):
        return node.name
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                return target.id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return None

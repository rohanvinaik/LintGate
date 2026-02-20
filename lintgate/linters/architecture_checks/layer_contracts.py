"""Layer contract enforcement: verify imports respect declared boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...types import LintIssue
from ._helpers import (
    build_layer_map,
    extract_imports,
    filepath_to_module,
    find_layer,
    module_matches_prefix,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ...types import LinterContext


def check_layer_contracts(
    files: list[str],
    layers: list[dict[str, Any]],
    ctx: LinterContext,
) -> Iterable[LintIssue]:
    """Verify import statements respect declared layer boundaries."""
    layer_map = build_layer_map(layers)

    for filepath in files:
        module_name = filepath_to_module(filepath, ctx.project_root)
        if not module_name:
            continue

        file_layer = find_layer(module_name, layer_map)
        if not file_layer:
            continue

        yield from _check_file_layer_imports(
            filepath,
            module_name,
            file_layer,
            layer_map,
        )


def _check_file_layer_imports(
    filepath: str,
    module_name: str,
    file_layer: dict[str, Any],
    layer_map: dict[str, dict[str, Any]],
) -> Iterable[LintIssue]:
    """Check a single file's imports against its layer's contract."""
    imports = extract_imports(filepath)
    must_not_import = set(file_layer.get("must_not_import", []))
    may_import = set(file_layer.get("may_import", []))
    layer_name = file_layer["name"]

    for imp_module, lineno in imports:
        yield from _check_forbidden_import(
            filepath,
            module_name,
            layer_name,
            imp_module,
            lineno,
            must_not_import,
        )
        if may_import:
            yield from _check_allowlist_import(
                filepath,
                module_name,
                layer_name,
                imp_module,
                lineno,
                may_import,
                file_layer,
                layer_map,
            )


def _check_forbidden_import(
    filepath: str,
    module_name: str,
    layer_name: str,
    imp_module: str,
    lineno: int,
    must_not_import: set[str],
) -> Iterable[LintIssue]:
    """Check if an import violates a must_not_import rule."""
    for forbidden in must_not_import:
        if module_matches_prefix(imp_module, forbidden):
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
    filepath: str,
    module_name: str,
    layer_name: str,
    imp_module: str,
    lineno: int,
    may_import: set[str],
    file_layer: dict[str, Any],
    layer_map: dict[str, dict[str, Any]],
) -> Iterable[LintIssue]:
    """Check if a cross-layer import is in the allowed list."""
    imp_layer = find_layer(imp_module, layer_map)
    if not imp_layer or imp_layer["name"] == file_layer["name"]:
        return

    allowed = any(module_matches_prefix(imp_module, prefix) for prefix in may_import)
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

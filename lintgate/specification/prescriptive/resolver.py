"""Target resolution for prescriptive specs.

Determines which functions deserve prescriptive specs via
explicit targets, PSPEC annotations, and claim-to-symbol matching.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

# ── Target Resolution ─────────────────────────────────────────────────

# Pattern for identifying Python symbols in prose
_SYMBOL_PATTERN = re.compile(
    r"\b([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*)\b"  # snake_case dotted names
    r"|"
    r"\b([A-Z][a-zA-Z0-9]*(?:\.[A-Z][a-zA-Z0-9]*)*)\b",  # CamelCase dotted names
)

# Pattern for PSPEC annotations on function defs
_PSPEC_ANNOTATION = re.compile(r"#\s*PSPEC:\s*(.+)")


@dataclass
class ResolvedTarget:
    """A target matched by the resolver, with provenance."""

    target_key: str  # module::function
    source: str  # "explicit" | "stub" | "claim_match"
    matched_claim: str
    confidence: float  # 1.0 for explicit, 0.8 for stub, variable for claim_match

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_key": self.target_key,
            "source": self.source,
            "matched_claim": self.matched_claim,
            "confidence": self.confidence,
        }


def resolve_targets(
    compass: Any,  # CompassState
    theory_profile: dict[str, Any],
    project_root: str,
    explicit_targets: list[str] | None = None,
) -> list[ResolvedTarget]:
    """Determine which functions deserve prescriptive specs.

    Strategy 1 — Explicit targets (confidence=1.0)
    Strategy 2 — Interface stubs with # PSPEC: annotations (confidence=0.8)
    Strategy 3 — Claim-to-symbol matching (confidence=variable, ≥0.5 to emit)
    """
    results: list[ResolvedTarget] = []
    seen: set[str] = set()

    # Strategy 1: Explicit targets
    if explicit_targets:
        for target in explicit_targets:
            if target not in seen:
                results.append(
                    ResolvedTarget(
                        target_key=target,
                        source="explicit",
                        matched_claim="user-specified",
                        confidence=1.0,
                    )
                )
                seen.add(target)

    # Strategy 2: Scan for PSPEC stubs
    stubs = _scan_pspec_stubs(project_root)
    for target_key, annotation in stubs:
        if target_key not in seen:
            results.append(
                ResolvedTarget(
                    target_key=target_key,
                    source="stub",
                    matched_claim=annotation,
                    confidence=0.8,
                )
            )
            seen.add(target_key)

    # Strategy 3: Claim-to-symbol matching
    claim_matches = _match_claims_to_symbols(compass, theory_profile, project_root, seen)
    results.extend(claim_matches)

    return results


def _scan_pspec_stubs(project_root: str) -> list[tuple[str, str]]:
    """Scan project for # PSPEC: annotations on stub functions."""
    stubs: list[tuple[str, str]] = []
    for root, dirs, files in os.walk(project_root):
        # Skip hidden dirs and common non-source dirs
        rel = os.path.relpath(root, project_root)
        if rel != ".":
            parts = rel.split(os.sep)
            if any(part.startswith(".") for part in parts):
                dirs.clear()
                continue
            if any(part in ("node_modules", "__pycache__", ".git") for part in parts):
                dirs.clear()
                continue
        # Prune hidden/system subdirs from further traversal
        dirs[:] = [
            d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__")
        ]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    source = f.read()
            except (OSError, UnicodeDecodeError):
                continue

            # Check for PSPEC annotations
            lines = source.split("\n")
            for i, line in enumerate(lines):
                m = _PSPEC_ANNOTATION.search(line)
                if not m:
                    continue
                annotation = m.group(1).strip()
                # Find the function def on the next few lines
                func_name = _find_function_at(source, i + 1)
                if func_name:
                    from lintgate.keys import canonical_function_key

                    relpath = os.path.relpath(fpath, project_root).replace(os.sep, "/")
                    target_key = canonical_function_key(relpath, func_name)
                    stubs.append((target_key, annotation))

    return stubs


def _find_function_at(source: str, annotation_line: int) -> str | None:
    """Find function name near annotation_line (0-indexed)."""
    import ast as _ast

    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return None

    for node in _ast.walk(tree):
        if (
            isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef))
            and abs(node.lineno - (annotation_line + 1)) <= 3
        ):
            return node.name
    return None


def _collect_compass_claims(compass: Any) -> list[tuple[str, float, str]]:
    """Collect claims from compass directives and axes."""
    items: list[tuple[str, float, str]] = []
    if hasattr(compass, "directives"):
        for i, d in enumerate(compass.directives):
            conf = 0.7 if d.kind == "toward" else 0.6
            items.append((d.text, conf, f"compass:{d.kind}:{i}"))
    if hasattr(compass, "axes"):
        for axis_name, axis in compass.axes.items():
            for j, claim in enumerate(axis.claims):
                items.append((claim.text, claim.confidence, f"compass:{axis_name}:{j}"))
    return items


def _collect_theory_claims(theory_profile: dict[str, Any]) -> list[tuple[str, float, str]]:
    """Collect claims from theory profile facets."""
    items: list[tuple[str, float, str]] = []
    for facet_name, facet_data in theory_profile.items():
        if not isinstance(facet_data, dict):
            continue
        for k, claim in enumerate(facet_data.get("claims", [])):
            text = claim.get("text", "") if isinstance(claim, dict) else str(claim)
            conf = claim.get("confidence", 0.7) if isinstance(claim, dict) else 0.7
            items.append((text, conf, f"theory:{facet_name}:{k}"))
    return items


def _collect_claim_items(
    compass: Any, theory_profile: dict[str, Any]
) -> list[tuple[str, float, str]]:
    """Collect all claim texts with confidence from compass and theory profile."""
    return _collect_compass_claims(compass) + _collect_theory_claims(theory_profile)


def _match_claims_to_symbols(
    compass: Any,
    theory_profile: dict[str, Any],
    project_root: str,
    seen: set[str],
) -> list[ResolvedTarget]:
    """Extract symbols from claims, match against project functions."""
    results: list[ResolvedTarget] = []
    claim_items = _collect_claim_items(compass, theory_profile)

    # Extract symbols from claims
    symbols_from_claims: list[tuple[str, float, str]] = []
    for text, conf, source in claim_items:
        if conf < 0.5:
            continue
        for match in _SYMBOL_PATTERN.finditer(text):
            sym = match.group(1) or match.group(2)
            if sym and len(sym) > 3 and sym.lower() not in _STOPWORDS:
                symbols_from_claims.append((sym, conf, source))

    # Build a simple function index from project
    func_index = _build_func_index(project_root)

    # Match symbols to functions
    for sym, conf, source in symbols_from_claims:
        for func_key in func_index:
            func_name = func_key.split("::")[-1] if "::" in func_key else func_key
            if sym == func_name or sym.endswith(f".{func_name}"):
                match_quality = 1.0 if sym == func_name else 0.8
                final_conf = conf * match_quality
                if final_conf >= 0.5 and func_key not in seen:
                    results.append(
                        ResolvedTarget(
                            target_key=func_key,
                            source="claim_match",
                            matched_claim=source,
                            confidence=round(final_conf, 2),
                        )
                    )
                    seen.add(func_key)

    return results


def _build_func_index(project_root: str) -> set[str]:
    """Build a set of function keys from project source files (shallow scan)."""
    import ast as _ast

    func_keys: set[str] = set()
    # Only scan top-level Python files and first-level packages
    for root, dirs, files in os.walk(project_root):
        rel = os.path.relpath(root, project_root)
        depth = len(rel.split(os.sep)) if rel != "." else 0
        if depth > 2:
            dirs.clear()
            continue
        if rel != "." and any(part.startswith(".") for part in rel.split(os.sep)):
            dirs.clear()
            continue
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for fname in files:
            if not fname.endswith(".py") or fname.startswith("test_"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    tree = _ast.parse(f.read())
            except (OSError, SyntaxError):
                continue
            relpath = os.path.relpath(fpath, project_root).replace(os.sep, "/")
            from lintgate.keys import canonical_function_key

            for node in _ast.walk(tree):
                if isinstance(
                    node, (_ast.FunctionDef, _ast.AsyncFunctionDef)
                ) and not node.name.startswith("_"):
                    func_keys.add(canonical_function_key(relpath, node.name))
    return func_keys


_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "are",
        "not",
        "but",
        "has",
        "have",
        "was",
        "were",
        "been",
        "being",
        "will",
        "should",
        "would",
        "could",
        "when",
        "where",
        "which",
        "what",
        "than",
        "then",
        "each",
        "every",
        "other",
        "some",
        "most",
        "more",
        "also",
        "just",
        "only",
        "both",
        "such",
        "very",
        "true",
        "false",
        "none",
        "self",
        "return",
        "class",
        "import",
        "def",
        "pass",
        "raise",
        "try",
        "except",
        "finally",
        "while",
        "else",
        "elif",
        "yield",
        "async",
        "await",
    }
)

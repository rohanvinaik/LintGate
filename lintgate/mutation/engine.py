"""Advanced mutation execution engine.

Orchestrates the execution of mutation testing, enabling a two-tier model
(inline sampling vs background profiling) and test-impact selection to reduce
execution time.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re as _re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    import libcst as cst
    from libcst.metadata import MetadataWrapper, PositionProvider
except ImportError:
    cst = None  # type: ignore

if TYPE_CHECKING:
    from lintgate.linters.performance_checks.manifest import PropertyManifest
    from lintgate.linters.test_effectiveness.types import TestEffectivenessManifest

import contextlib

from lintgate.mutation.policy import (
    MutationOperatorCategory,
    MutationTelemetry,
    OperatorRelevanceMatrix,
    RuntimeBudget,
)
from lintgate.mutation.state import (
    CoverageDepth,
    FunctionMutationState,
    MutationStateManager,
    SignalQuality,
    SurvivorSite,
    compute_content_hash,
)

logger = logging.getLogger(__name__)


# Thresholds for deterministic signal quality classification
# These define the boundary between sampled_low and sampled_high
_SAMPLED_HIGH_MUTANT_THRESHOLD = 10  # Minimum mutants for high quality
_SAMPLED_HIGH_CATEGORY_THRESHOLD = 2  # Minimum categories covered for high quality


def _classify_sampled_quality(
    total_mutants: int,
    category_count: int,
    timeout_ratio: float,
) -> SignalQuality:
    """Classify sampled run as low or high quality deterministically.

    Uses measurable run signals to determine signal quality:
    - More mutants = higher quality (more coverage)
    - More category coverage = higher quality
    - Lower timeout ratio = higher quality (run completed successfully)

    Args:
        total_mutants: Total number of mutants generated
        category_count: Number of mutation categories covered
        timeout_ratio: Ratio of timeouts to total mutants (0.0-1.0)

    Returns:
        SignalQuality.SAMPLED_LOW or SignalQuality.SAMPLED_HIGH
    """
    # Default to low quality for invalid inputs
    if total_mutants <= 0 or category_count <= 0:
        return SignalQuality.SAMPLED_LOW

    # Check thresholds for high quality
    has_sufficient_mutants = total_mutants >= _SAMPLED_HIGH_MUTANT_THRESHOLD
    has_sufficient_categories = category_count >= _SAMPLED_HIGH_CATEGORY_THRESHOLD
    has_low_timeout_ratio = timeout_ratio < 0.1  # Less than 10% timeouts

    # High quality requires sufficient mutants AND either good category coverage OR low timeouts
    if has_sufficient_mutants and (has_sufficient_categories or has_low_timeout_ratio):
        return SignalQuality.SAMPLED_HIGH

    return SignalQuality.SAMPLED_LOW


# Mapping from TEFF assertion kinds to mutation operator categories
# This is used to determine which categories are "covered" by strong assertions
ASSERTION_KIND_TO_MUTATION_CATEGORY: dict[str, str] = {
    # Exact value assertions cover arithmetic/number categories
    "exact_value": "arithmetic",
    "equality": "arithmetic",
    "comparison": "conditional",
    # Range/length checks cover conditional/boundary categories
    "range_check": "conditional",
    "length_check": "conditional",
    # String assertions cover string category
    "string_equality": "string",
    "regex_match": "string",
    "substring": "string",
    # Boolean assertions cover keyword category
    "is_true": "keyword",
    "is_false": "keyword",
    # Type checks are structural (weak) so not mapped
    "isinstance_check": None,
    "hasattr_check": None,
    "is_none": None,
    "is_not_none": None,
}


def _map_assertion_kind_to_category(assertion_kind: str) -> str | None:
    """Map a TEFF assertion kind to a mutation operator category.

    Args:
        assertion_kind: The assertion kind from TEFF (e.g., 'exact_value', 'comparison')

    Returns:
        The mutation category string or None if the assertion doesn't cover a category
    """
    return ASSERTION_KIND_TO_MUTATION_CATEGORY.get(assertion_kind)


class MutationEngine:
    """Orchestrates mutation testing execution."""

    def __init__(
        self,
        state_manager: MutationStateManager,
        budget: RuntimeBudget,
    ):
        self.state_manager = state_manager
        self.budget = budget
        self.relevance_matrix = OperatorRelevanceMatrix()

    def run_inline_sampling(
        self,
        target_files: list[str],
        telemetry: MutationTelemetry,
        algebra_manifest: PropertyManifest | None = None,
        teff_manifest: TestEffectivenessManifest | None = None,
    ) -> list[FunctionMutationState]:
        """Run a fast, inline sampled mutation run on specific files.

        This is Tier 1: Designed to run as part of active gating or direct developer
        feedback loops. It uses a strict time budget and limits mutants per function.
        """
        if not self.budget.enabled:
            return []

        results = []
        for file_path in target_files:
            # Check budgets
            if telemetry.inline_time_ms_spent >= self.budget.max_inline_ms_per_function * len(
                target_files
            ):
                logger.warning(f"Mutation inline budget exhausted. Skipping {file_path}")
                break

            relevant_categories, covered_skips, covered_categories = self._collect_file_categories(
                file_path, algebra_manifest, teff_manifest
            )
            if covered_skips > 0:
                telemetry.mutants_skipped_covered += covered_skips

            start_t = time.perf_counter()
            success = self._execute_mutmut(
                paths=[file_path],
                depth=CoverageDepth.SAMPLED,
                test_filter=None,
                relevant_categories=relevant_categories,
                covered_categories=covered_categories,
                telemetry=telemetry,
            )
            elapsed_ms = (time.perf_counter() - start_t) * 1000
            telemetry.add_inline_time(elapsed_ms)

            if success:
                file_states = self._parse_mutmut_results([file_path])
                for state in file_states.values():
                    state.depth = CoverageDepth.SAMPLED
                    # Classify signal quality for sampled runs
                    category_count = len(state.survived_by_category)
                    timeout_ratio = state.timeout / state.total if state.total > 0 else 0.0
                    state.signal_quality = _classify_sampled_quality(
                        state.total, category_count, timeout_ratio
                    )
                    # Track signal quality in telemetry
                    if telemetry:
                        if state.signal_quality == SignalQuality.SAMPLED_HIGH:
                            telemetry.sampled_high_runs += 1
                        else:
                            telemetry.sampled_low_runs += 1
                    self.state_manager.update_state(state)
                    results.append(state)

        self.state_manager.save()
        return results

    def run_background_profiling(
        self,
        target_files: list[str],
        test_mapping: dict[str, list[str]],
        telemetry: MutationTelemetry,
        algebra_manifest: PropertyManifest | None = None,
        teff_manifest: TestEffectivenessManifest | None = None,
    ) -> list[FunctionMutationState]:
        """Run deep, background mutation profiling.

        This is Tier 2: Designed to run on CI or in background agents. It uses the
        test-impact mapping to only run relevant tests for each mutated file, massively
        speeding up exhaustive sweeps.
        """
        if not self.budget.enabled:
            return []

        results = []
        for file_path in target_files:
            relevant_tests = test_mapping.get(file_path, [])

            # Test-impact selection requirement (Item 6)
            test_filter = " ".join(relevant_tests) if relevant_tests else None
            if not test_filter:
                logger.info(
                    f"Fallback reason: No test impact mapping found for {file_path}. Running full suite."
                )

            relevant_categories, covered_skips, covered_categories = self._collect_file_categories(
                file_path, algebra_manifest, teff_manifest
            )
            if covered_skips > 0:
                telemetry.mutants_skipped_covered += covered_skips

            success = self._execute_mutmut(
                paths=[file_path],
                depth=CoverageDepth.PROFILED,
                test_filter=test_filter,
                relevant_categories=relevant_categories,
                covered_categories=covered_categories,
                telemetry=telemetry,
            )
            telemetry.background_functions_profiled += 1

            if success:
                file_states = self._parse_mutmut_results([file_path])
                for state in file_states.values():
                    state.depth = CoverageDepth.PROFILED
                    # Profiled runs always get PROFILED quality
                    state.signal_quality = SignalQuality.PROFILED
                    # Track in telemetry
                    if telemetry:
                        telemetry.profiled_runs += 1
                    self.state_manager.update_state(state)
                    results.append(state)

        self.state_manager.save()
        return results

    def _execute_mutmut(
        self,
        paths: list[str],
        depth: CoverageDepth,
        test_filter: str | None,
        relevant_categories: set[MutationOperatorCategory] | None = None,
        covered_categories: set[MutationOperatorCategory] | None = None,
        telemetry: MutationTelemetry | None = None,
    ) -> bool:
        """Execute mutmut v3 via subprocess.

        mutmut v3 reads paths_to_mutate from pyproject.toml, not CLI flags.
        We temporarily override pyproject.toml to scope the run, then restore it.

        Returns True if successful (mutants killed/survived normally), False on runner crash.
        """
        pyproject_path = Path("pyproject.toml")
        original_pyproject = None
        if pyproject_path.exists():
            original_pyproject = pyproject_path.read_text("utf-8")

        try:
            # Determine if we can do pre-execution filtering
            mutants_to_run = []
            filter_active = False

            if relevant_categories is not None and cst:
                filter_active = True
                # Default to empty set if not provided
                covered = covered_categories if covered_categories else set()
                for path in paths:
                    try:
                        source = Path(path).read_text("utf-8")
                        cat_map = self._build_mutant_category_map(path, source)
                        for mutant_id, cat in cat_map.items():
                            if cat in relevant_categories:
                                mutants_to_run.append(mutant_id)
                            else:
                                # Only count as policy skip if NOT a covered category
                                # Covered categories are tracked separately in mutants_skipped_covered
                                if cat not in covered and telemetry:
                                    telemetry.mutants_skipped_policy += 1
                    except Exception as e:
                        logger.debug(f"Failed to generate explicit mutant list for {path}: {e}")
                        filter_active = False
                        break

            # Rewrite [tool.mutmut] paths_to_mutate to scope to target files
            if original_pyproject and paths:
                scoped_paths = json.dumps(paths)
                new_content = _re.sub(
                    r"(paths_to_mutate\s*=\s*)\[[^\]]*\]",
                    f"paths_to_mutate = {scoped_paths}",
                    original_pyproject,
                )
                pyproject_path.write_text(new_content, "utf-8")

            max_children = min(self.budget.max_workers, 2)
            cmd = ["mutmut", "run", "--max-children", str(max_children)]

            # Wire test_filter into pytest args to limit test scope
            if test_filter:
                cmd.extend(["--pytest-args", test_filter])

            if filter_active:
                if not mutants_to_run:
                    # Everything was filtered out! We don't even need to run mutmut.
                    return True
                cmd.extend(mutants_to_run)

            proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=300)
            # mutmut v3: 0 = all killed, 2 = survivors found, 1 = other
            if proc.returncode in (0, 1, 2):
                if telemetry and filter_active:
                    telemetry.mutants_executed += len(mutants_to_run)
                return True
            return False
        except (subprocess.SubprocessError, FileNotFoundError):
            return False
        finally:
            if original_pyproject is not None:
                pyproject_path.write_text(original_pyproject, "utf-8")

    def _collect_file_categories(
        self,
        file_path: str,
        algebra_manifest: PropertyManifest | None = None,
        teff_manifest: TestEffectivenessManifest | None = None,
    ) -> tuple[set[MutationOperatorCategory] | None, int, set[MutationOperatorCategory]]:
        """Walk a file's AST to collect the union of relevant categories for all functions.

        Returns:
            Tuple of (categories set or None, total skip count from covered categories, covered categories set)
        """
        try:
            source = Path(file_path).read_text("utf-8")
            tree = ast.parse(source)
            file_categories: set[MutationOperatorCategory] = set()
            total_skips = 0
            all_covered: set[MutationOperatorCategory] = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    cat, skip_count, covered = self._compute_relevant_categories(
                        file_path, node.name, algebra_manifest, teff_manifest
                    )
                    file_categories.update(cat)
                    total_skips += skip_count
                    all_covered.update(covered)
            return file_categories, total_skips, all_covered
        except (OSError, SyntaxError):
            return None, 0, set()

    def _compute_relevant_categories(
        self,
        file_path: str,
        func_name: str,
        algebra_manifest: PropertyManifest | None = None,
        teff_manifest: TestEffectivenessManifest | None = None,
    ) -> tuple[set[MutationOperatorCategory], int, set[MutationOperatorCategory]]:
        """Layer 1: Exclusionary filtering.

        Returns:
            Tuple of (relevant categories, estimated mutants skipped due to covered categories, covered categories set)
        """
        is_pure = False
        if algebra_manifest:
            from lintgate.linters.performance_checks.algebra_types import PropertyKind

            for key, props in algebra_manifest.functions.items():
                if key.endswith(f"::{func_name}"):
                    is_pure = any(p.kind == PropertyKind.PURE for p in props.properties)
                    break

        branch_count = 0
        has_strings = False
        has_numbers = False

        if os.path.exists(file_path):
            try:
                source = Path(file_path).read_text("utf-8")
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if (
                        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name == func_name
                    ):
                        visitor = FunctionCharacteristicVisitor()
                        visitor.visit(node)
                        branch_count = visitor.branch_count
                        has_strings = visitor.has_strings
                        has_numbers = visitor.has_numbers
                        break
            except (OSError, SyntaxError):
                pass

        # Determine categories currently "covered" by strong existing assertions
        covered_categories = set()
        mutants_skipped_covered_count = 0
        if teff_manifest and os.path.exists(file_path):
            # Find function in teff manifest - key is function_name or file_path::function_name
            if file_path in teff_manifest.functions:
                # Try file_path directly
                func_effect = teff_manifest.functions.get(file_path)
            elif f"{file_path}::{func_name}" in teff_manifest.functions:
                func_effect = teff_manifest.functions.get(f"{file_path}::{func_name}")
            else:
                func_effect = None

            if func_effect:
                # Map strong assertions to mutation categories
                from lintgate.linters.test_effectiveness.types import SEMANTIC_STRENGTH_THRESHOLD

                for assertion in func_effect.assertions:
                    if assertion.strength >= SEMANTIC_STRENGTH_THRESHOLD:
                        # Map assertion kind to mutation category
                        covered_cat = _map_assertion_kind_to_category(assertion.kind.value)
                        if covered_cat:
                            covered_categories.add(covered_cat)

                # Estimate mutants skipped based on covered categories
                # This is a heuristic: covered categories reduce relevant mutants
                if covered_categories:
                    mutants_skipped_covered_count = len(covered_categories) * 2  # Estimate

        return (
            self.relevance_matrix.get_prioritized_categories(
                is_pure=is_pure,
                branch_count=branch_count,
                has_strings=has_strings,
                has_numbers=has_numbers,
                covered_categories=covered_categories,
            ),
            mutants_skipped_covered_count,
            covered_categories,
        )

    def _parse_mutmut_results(self, paths: list[str]) -> dict[str, FunctionMutationState]:
        """Parse mutmut v3 results and return per-function state.

        mutmut v3 output format:
            module.x__funcname__mutmut_N: killed|survived|timeout|not checked

        We demangle the function name, aggregate per-function, and filter to
        only the requested paths.
        """
        cmd = ["mutmut", "results", "--all", "true"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                return {}
            output = proc.stdout
        except (subprocess.SubprocessError, FileNotFoundError):
            return {}

        # 0. Build a category map for mutants in these paths.
        #    Uses mutmut+libcst when available, with a built-in AST fallback.
        mutant_category_map: dict[str, str] = {}
        # Also build detailed info map with line/operator for survivor_sites
        mutant_info_map: dict[str, dict[str, Any]] = {}
        for path in paths:
            if os.path.exists(path):
                try:
                    source = Path(path).read_text("utf-8")
                    mutant_category_map.update(self._build_mutant_category_map(path, source))
                    mutant_info_map.update(self._get_mutant_info(path, source))
                except Exception as e:
                    logger.debug(f"Failed to build category map for {path}: {e}")

        # Parse mutmut v3 output lines and aggregate per function
        func_counts: dict[str, dict[str, Any]] = {}

        for line in output.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue

            name_part, _, status = line.rpartition(":")
            name_part = name_part.strip()
            status = status.strip()

            if status == "not checked":
                continue

            # Strip __mutmut_N suffix to get the mangled function name
            func_mangled = _re.sub(r"__mutmut_\d+$", "", name_part)

            if func_mangled not in func_counts:
                func_counts[func_mangled] = {
                    "killed": 0,
                    "survived": 0,
                    "timeout": 0,
                    "total": 0,
                    "survived_by_category": {},
                    "survivor_sites": [],  # Track detailed survivor info
                }

            func_counts[func_mangled]["total"] += 1
            if status == "killed":
                func_counts[func_mangled]["killed"] += 1
            elif status == "survived":
                func_counts[func_mangled]["survived"] += 1
                # Try to lookup category
                cat = mutant_category_map.get(name_part)
                if cat:
                    func_counts[func_mangled]["survived_by_category"][cat] = (
                        func_counts[func_mangled]["survived_by_category"].get(cat, 0) + 1
                    )

                # Collect survivor site info if available
                info = mutant_info_map.get(name_part)
                if info:
                    # Valid info with line/operator
                    site = SurvivorSite(
                        line=info.get("line", -1),
                        column=0,
                        category=info.get("category", "unknown"),
                        mutant_id=name_part.split("__mutmut_")[-1]
                        if "__mutmut_" in name_part
                        else "unknown",
                        operator=info.get("operator", "unknown"),
                    )
                    func_counts[func_mangled]["survivor_sites"].append(site)
                else:
                    # Sentinel for unknown location
                    site = SurvivorSite(
                        line=-1,
                        column=0,
                        category="unknown",
                        mutant_id=name_part.split("__mutmut_")[-1]
                        if "__mutmut_" in name_part
                        else "unknown",
                        operator="unknown",
                    )
                    func_counts[func_mangled]["survivor_sites"].append(site)
            elif status == "timeout":
                func_counts[func_mangled]["timeout"] += 1

        # Demangle and filter to requested paths
        states: dict[str, FunctionMutationState] = {}

        for mangled, counts in func_counts.items():
            if counts["total"] == 0:
                continue

            # Demangle and filter to requested paths
            file_path, func_name = _demangle_mutmut_name(mangled)
            if not file_path or not func_name:
                continue

            # Map the demangled file_path back to an entry in 'paths'
            matched_path = None
            if paths:
                for p in paths:
                    abs_p = os.path.abspath(p)
                    # Use endswith check to handle absolute vs relative or missing leading slash
                    if abs_p.endswith(os.path.normpath(file_path)):
                        matched_path = abs_p
                        break

                if not matched_path:
                    continue
            else:
                matched_path = file_path

            code_content = ""
            if os.path.exists(matched_path):
                with contextlib.suppress(OSError):
                    code_content = Path(matched_path).read_text("utf-8")

            func_id = f"{matched_path}::{func_name}"
            states[func_id] = FunctionMutationState(
                function_name=func_name,
                file_path=matched_path,
                code_hash=compute_content_hash(code_content),
                test_hash="unknown",
                depth=CoverageDepth.PROFILED,
                killed=counts["killed"],
                survived=counts["survived"],
                timeout=counts["timeout"],
                total=counts["total"],
                survived_by_category=counts["survived_by_category"],
                survivor_sites=sorted(
                    counts["survivor_sites"],
                    key=lambda s: (s.line, s.category, s.mutant_id),
                ),
            )

        return states

    def _build_mutant_category_map(self, path: str, source: str) -> dict[str, str]:
        """Build mutant-id -> category map for a module.

        Prefers mutmut's native operator stream (when available) and falls back to
        a lightweight AST-based approximation when optional dependencies are missing.
        """
        module_name = os.path.splitext(path)[0].replace("/", ".")
        if module_name.startswith("."):
            module_name = module_name[1:]

        # Preferred path: match mutmut internals as closely as possible.
        native_map = self._build_mutant_category_map_with_mutmut(module_name, source)
        if native_map:
            return native_map

        # Fallback path: ensure deterministic category mapping even without mutmut/libcst.
        return self._build_mutant_category_map_with_ast(module_name, source)

    def _get_mutant_info(self, path: str, source: str) -> dict[str, dict[str, Any]]:
        """Build mutant-id -> detailed info (category, line, operator) map.

        Returns a dict mapping mutant_id to:
        - category: mutation category (arithmetic, conditional, etc.)
        - line: source line number (1-indexed)
        - operator: mutmut operator name or "unknown"
        """
        module_name = os.path.splitext(path)[0].replace("/", ".")
        if module_name.startswith("."):
            module_name = module_name[1:]

        # Try mutmut+libcst path first
        info = self._get_mutant_info_with_cst(module_name, source)
        if info:
            return info

        # Fallback to AST-based
        return self._get_mutant_info_with_ast(module_name, source)

    def _get_mutant_info_with_cst(self, module_name: str, source: str) -> dict[str, dict[str, Any]]:
        """Get mutant info using libcst when available.

        Note: Full libcst integration with mutmut requires more complex setup.
        Currently returns empty dict to fall through to AST-based method.
        """
        # libcst integration would require mutmut internals that aren't easily accessible
        # Fall through to AST-based method for actual implementation
        return {}

    def _get_mutant_info_with_ast(self, module_name: str, source: str) -> dict[str, dict[str, Any]]:
        """Get mutant info using AST fallback (returns category and line)."""
        class_sep = "\u01c1"

        def _is_string_expr(node: ast.AST) -> bool:
            return isinstance(node, ast.JoinedStr) or (
                isinstance(node, ast.Constant) and isinstance(node.value, str)
            )

        def _infer_category(node: ast.AST) -> str | None:
            if isinstance(node, ast.BinOp) and isinstance(
                node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
            ):
                if _is_string_expr(node.left) or _is_string_expr(node.right):
                    return "string"
                return "arithmetic"
            if isinstance(node, ast.BoolOp):
                return "conditional"
            if isinstance(node, ast.Compare):
                return "conditional"
            if isinstance(node, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.Match)):
                return "conditional"
            if isinstance(node, ast.Constant):
                if isinstance(node.value, str):
                    return "string"
                if isinstance(node.value, (int, float, complex)):
                    return "number"
            if isinstance(node, ast.Assign):
                return "keyword"
            return None

        def _operator_name(node: ast.AST) -> str:
            """Get a simplified operator name for the node."""
            if isinstance(node, ast.BinOp):
                return type(node.op).__name__.lower()
            if isinstance(node, ast.BoolOp):
                return type(node.op).__name__.lower()
            if isinstance(node, ast.Compare):
                return "compare"
            if isinstance(node, (ast.If, ast.IfExp)):
                return "conditional"
            if isinstance(node, ast.Assign):
                return "assignment"
            return "unknown"

        def _iter_function_nodes(node: ast.AST):
            for child in ast.iter_child_nodes(node):
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
                ):
                    continue
                yield child
                yield from _iter_function_nodes(child)

        class InfoVisitor(ast.NodeVisitor):
            def __init__(self):
                self.id_map: dict[str, dict[str, Any]] = {}
                self.class_stack: list[str] = []
                self.func_mutant_counts: dict[str, int] = {}

            def visit_ClassDef(self, node: ast.ClassDef):
                self.class_stack.append(node.name)
                self.generic_visit(node)
                self.class_stack.pop()

            def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
                if self.class_stack:
                    base = f"x{class_sep}{self.class_stack[-1]}{class_sep}{node.name}"
                else:
                    base = f"x_{node.name}"

                for child in _iter_function_nodes(node):
                    category = _infer_category(child)
                    if not category:
                        continue

                    next_index = self.func_mutant_counts.get(base, 0) + 1
                    self.func_mutant_counts[base] = next_index

                    full_key = f"{module_name}.{base}__mutmut_{next_index}"

                    # Get line number (1-indexed from AST)
                    line = getattr(child, "lineno", -1)
                    if line is None:
                        line = -1

                    operator = _operator_name(child)

                    self.id_map[full_key] = {
                        "category": category,
                        "line": line,
                        "operator": operator,
                    }

                self.generic_visit(node)

            def visit_FunctionDef(self, node: ast.FunctionDef):
                self._visit_function(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
                self._visit_function(node)

        try:
            tree = ast.parse(source)
            visitor = InfoVisitor()
            visitor.visit(tree)
            return visitor.id_map
        except (SyntaxError, ValueError, TypeError) as e:
            logger.debug(f"Exception in _get_mutant_info_with_ast for {module_name}: {e}")
            return {}

    def _build_mutant_category_map_with_mutmut(
        self, module_name: str, source: str
    ) -> dict[str, str]:
        if not cst:
            return {}
        try:
            module = cst.parse_module(source)
            wrapper = MetadataWrapper(module)

            # Replicate mutmut's mangling and ordering
            # Note: This is a simplified version of mutmut's internal logic
            # specifically for identifying categories.
            from mutmut.node_mutation import mutation_operators
            from mutmut.trampoline_templates import mangle_function_name

            # We need an OuterFunctionProvider if we wanted to match mutmut exactly,
            # but for category mapping, we just need to know which operator was applied.

            class MappingVisitor(cst.CSTVisitor):
                METADATA_DEPENDENCIES = (PositionProvider,)

                def __init__(self, operators):
                    self.mutants: list[tuple[str, str]] = []  # (mangled_base, category)
                    self._operators = operators
                    self._stack: list[tuple[str, str | None]] = []  # (type, name)

                def on_visit(self, node: cst.CSTNode) -> bool:
                    if isinstance(node, cst.ClassDef):
                        self._stack.append(("class", node.name.value))
                    elif isinstance(node, cst.FunctionDef):
                        class_name = next(
                            (s[1] for s in reversed(self._stack) if s[0] == "class"), None
                        )
                        mangled = mangle_function_name(name=node.name.value, class_name=class_name)
                        self._stack.append(("func", mangled))

                    if isinstance(node, (cst.Annotation, cst.Decorator)):
                        return False

                    current_func = next(
                        (s[1] for s in reversed(self._stack) if s[0] == "func"), None
                    )
                    if current_func:
                        for t, operator in self._operators:
                            if isinstance(node, t):
                                try:
                                    inventions = list(operator(node))
                                    if inventions:
                                        op_name = operator.__name__
                                        cat = self._map_op_name_to_category(op_name, node)
                                        for _ in inventions:
                                            self.mutants.append((current_func, cat))
                                except Exception:
                                    continue
                    return True

                def on_leave(self, node: cst.CSTNode):
                    if isinstance(node, (cst.ClassDef, cst.FunctionDef)) and self._stack:
                        self._stack.pop()

                def _map_op_name_to_category(self, op_name: str, node: Any) -> str:
                    if "number" in op_name:
                        return "number"
                    if "string" in op_name:
                        return "string"
                    if "assignment" in op_name:
                        return "keyword"
                    if "keyword" in op_name:
                        return "keyword"
                    if "swap_op" in op_name and hasattr(node, "operator"):
                        # This is a bit deep, but we can guess
                        if isinstance(
                            node.operator,
                            (cst.Plus, cst.Minus, cst.Add, cst.Subtract, cst.Multiply, cst.Divide),
                        ):
                            return "arithmetic"
                        return "conditional"
                    return "other"

            visitor = MappingVisitor(mutation_operators)
            wrapper.visit(visitor)

            id_map: dict[str, str] = {}
            # Collect mutants per function
            func_mutant_counts: dict[str, int] = {}
            for base, cat in visitor.mutants:
                func_mutant_counts[base] = func_mutant_counts.get(base, 0) + 1
                n = func_mutant_counts[base]
                full_key = f"{module_name}.{base}__mutmut_{n}"
                id_map[full_key] = cat

            return id_map
        except Exception as e:
            logger.debug(
                f"Exception in _build_mutant_category_map_with_mutmut for {module_name}: {e}"
            )
            return {}

    def _build_mutant_category_map_with_ast(self, module_name: str, source: str) -> dict[str, str]:
        """Approximate mutmut category IDs using plain AST traversal."""

        class_sep = "\u01c1"

        def _is_string_expr(node: ast.AST) -> bool:
            return isinstance(node, ast.JoinedStr) or (
                isinstance(node, ast.Constant) and isinstance(node.value, str)
            )

        def _infer_category(node: ast.AST) -> str | None:
            if isinstance(node, ast.BinOp) and isinstance(
                node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
            ):
                if _is_string_expr(node.left) or _is_string_expr(node.right):
                    return "string"
                return "arithmetic"
            if isinstance(node, ast.BoolOp):
                return "conditional"
            if isinstance(node, ast.Compare):
                return "conditional"
            if isinstance(node, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.Match)):
                return "conditional"
            if isinstance(node, ast.Constant):
                if isinstance(node.value, str):
                    return "string"
                if isinstance(node.value, (int, float, complex)):
                    return "number"
            if isinstance(node, ast.Assign):
                return "keyword"
            return None

        def _iter_function_nodes(node: ast.AST):
            for child in ast.iter_child_nodes(node):
                # Skip nested functions/classes so each function gets its own IDs.
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
                ):
                    continue
                yield child
                yield from _iter_function_nodes(child)

        class FallbackVisitor(ast.NodeVisitor):
            def __init__(self):
                self.id_map: dict[str, str] = {}
                self.class_stack: list[str] = []
                self.func_mutant_counts: dict[str, int] = {}

            def visit_ClassDef(self, node: ast.ClassDef):  # noqa: N802
                self.class_stack.append(node.name)
                self.generic_visit(node)
                self.class_stack.pop()

            def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
                if self.class_stack:
                    base = f"x{class_sep}{self.class_stack[-1]}{class_sep}{node.name}"
                else:
                    base = f"x_{node.name}"

                for child in _iter_function_nodes(node):
                    category = _infer_category(child)
                    if not category:
                        continue
                    next_index = self.func_mutant_counts.get(base, 0) + 1
                    self.func_mutant_counts[base] = next_index
                    full_key = f"{module_name}.{base}__mutmut_{next_index}"
                    self.id_map[full_key] = category

                # Continue traversal for nested defs so they are handled separately.
                self.generic_visit(node)

            def visit_FunctionDef(self, node: ast.FunctionDef):  # noqa: N802
                self._visit_function(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):  # noqa: N802
                self._visit_function(node)

        try:
            tree = ast.parse(source)
            visitor = FallbackVisitor()
            visitor.visit(tree)
            return visitor.id_map
        except (SyntaxError, ValueError, TypeError) as e:
            logger.debug(f"Exception in _build_mutant_category_map_with_ast for {module_name}: {e}")
            return {}


def _demangle_mutmut_name(mangled: str) -> tuple[str, str]:
    """Convert mutmut v3 mangled name to (file_path, function_name).

    mutmut v3 mangles as:
      Module-level: module.path.x_funcname
      Class method: module.path.x\u01c1ClassName\u01c1method_name

    The separator is '.x' followed by either '_' (module-level) or
    '\u01c1' (class method, U+01C1 Latin Letter Lateral Click).

    Examples:
        'lintgate.mutation.ci_stats.x_compute_badge_color'
            -> ('lintgate/mutation/ci_stats.py', 'compute_badge_color')
        'lintgate.mutation.ci_stats.x__enrich_function_names'
            -> ('lintgate/mutation/ci_stats.py', '_enrich_function_names')
        'lintgate.linters.purity.x\u01c1_PureFunctionVisitor\u01c1visit_Call'
            -> ('lintgate/linters/purity.py', '_PureFunctionVisitor.visit_Call')
    """
    class_sep = "\u01c1"  # mutmut's CLASS_NAME_SEPARATOR

    # Class method format: .xǁClassǁmethod
    class_idx = mangled.rfind(".x" + class_sep)
    if class_idx != -1:
        module_dotted = mangled[:class_idx]
        remainder = mangled[class_idx + 2 :]  # skip '.x'
        # remainder is like 'ǁClassǁmethod' — split on separator
        parts = remainder.split(class_sep)
        # parts = ['', 'ClassName', 'method_name']
        parts = [p for p in parts if p]
        func_name = ".".join(parts) if len(parts) >= 2 else (parts[0] if parts else "")
        file_path = module_dotted.replace(".", "/") + ".py"
        return (file_path, func_name)

    # Module-level format: .x_funcname
    idx = mangled.rfind(".x_")
    if idx == -1:
        return ("", "")
    module_dotted = mangled[:idx]
    func_name = mangled[idx + 3 :]  # skip '.x_'
    file_path = module_dotted.replace(".", "/") + ".py"
    return (file_path, func_name)


class FunctionCharacteristicVisitor(ast.NodeVisitor):
    """AST visitor to extract structural characteristics for mutation policy."""

    def __init__(self):
        self.branch_count = 0
        self.has_strings = False
        self.has_numbers = False

    def _count_branch(self, node: ast.AST) -> None:
        self.branch_count += 1
        self.generic_visit(node)

    def visit_If(self, node: ast.If):
        self._count_branch(node)

    def visit_While(self, node: ast.While):
        self._count_branch(node)

    def visit_For(self, node: ast.For):
        self._count_branch(node)

    def visit_BoolOp(self, node: ast.BoolOp):
        # and/or also represent branching/logic complexity
        self._count_branch(node)

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str):
            self.has_strings = True
        elif isinstance(node.value, (int, float)):
            self.has_numbers = True

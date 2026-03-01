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
    compute_content_hash,
)

logger = logging.getLogger(__name__)


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
        project_root: str | None = None,
    ) -> list[FunctionMutationState]:
        """Run a fast, inline sampled mutation run on specific files.

        This is Tier 1: Designed to run as part of active gating or direct developer
        feedback loops. It uses a strict time budget and limits mutants per function.

        When *project_root* is provided, state keys use canonical identity
        (``relpath::qualname``) matching the manifest convention.
        """
        if not self.budget.enabled:
            return []

        results = []
        for file_path in target_files:
            # Check budgets
            if (
                telemetry.inline_time_ms_spent
                >= self.budget.max_inline_ms_per_function * len(target_files)
            ):
                logger.warning(
                    f"Mutation inline budget exhausted. Skipping {file_path}"
                )
                break

            # Heuristic: discover functions in file to compute per-function relevance.
            # Phase 4: Build per-function category maps instead of unioning all
            # functions' categories into a single file-level set. This avoids
            # including irrelevant mutant categories for functions that don't need them.
            relevant_categories = None
            per_function_categories: dict[str, set[MutationOperatorCategory]] | None = None
            try:
                source = Path(file_path).read_text("utf-8")
                tree = ast.parse(source)
                pfc: dict[str, set[MutationOperatorCategory]] = {}
                file_categories = set()
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        cat = self._compute_relevant_categories(
                            file_path, node.name, algebra_manifest, teff_manifest
                        )
                        pfc[node.name] = cat
                        file_categories.update(cat)
                relevant_categories = file_categories
                per_function_categories = pfc
            except (OSError, SyntaxError):
                pass

            start_t = time.perf_counter()
            success = self._execute_mutmut(
                paths=[file_path],
                depth=CoverageDepth.SAMPLED,
                test_filter=None,
                relevant_categories=relevant_categories,
                per_function_categories=per_function_categories,
                telemetry=telemetry,
            )
            elapsed_ms = (time.perf_counter() - start_t) * 1000
            telemetry.add_inline_time(elapsed_ms)

            if success:
                file_states = self._parse_mutmut_results(
                    [file_path], project_root=project_root
                )
                for state in file_states.values():
                    state.depth = CoverageDepth.SAMPLED
                    self.state_manager.update_state(
                        state, project_root=project_root
                    )
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
        project_root: str | None = None,
    ) -> list[FunctionMutationState]:
        """Run deep, background mutation profiling.

        This is Tier 2: Designed to run on CI or in background agents. It uses the
        test-impact mapping to only run relevant tests for each mutated file, massively
        speeding up exhaustive sweeps.

        When *project_root* is provided, state keys use canonical identity
        (``relpath::qualname``) matching the manifest convention.
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

            # Compute relevance for background profiling as well
            relevant_categories = None
            try:
                source = Path(file_path).read_text("utf-8")
                tree = ast.parse(source)
                file_categories = set()
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        cat = self._compute_relevant_categories(
                            file_path, node.name, algebra_manifest, teff_manifest
                        )
                        file_categories.update(cat)
                relevant_categories = file_categories
            except (OSError, SyntaxError):
                pass

            success = self._execute_mutmut(
                paths=[file_path],
                depth=CoverageDepth.PROFILED,
                test_filter=test_filter,
                relevant_categories=relevant_categories,
                telemetry=telemetry,
            )
            telemetry.background_functions_profiled += 1

            if success:
                file_states = self._parse_mutmut_results(
                    [file_path], project_root=project_root
                )
                for state in file_states.values():
                    state.depth = CoverageDepth.PROFILED
                    self.state_manager.update_state(
                        state, project_root=project_root
                    )
                    results.append(state)

        self.state_manager.save()
        return results

    def _execute_mutmut(
        self,
        paths: list[str],
        depth: CoverageDepth,
        test_filter: str | None,
        relevant_categories: set[MutationOperatorCategory] | None = None,
        per_function_categories: dict[str, set[MutationOperatorCategory]] | None = None,
        telemetry: MutationTelemetry | None = None,
    ) -> bool:
        """Execute mutmut v3 via subprocess.

        mutmut v3 reads paths_to_mutate from pyproject.toml, not CLI flags.
        We temporarily override pyproject.toml to scope the run, then restore it.

        When *per_function_categories* is provided, mutant filtering is done at
        function granularity (Phase 4) — each mutant is only included if its
        category is relevant for the specific function it belongs to. Falls back
        to file-level *relevant_categories* when per-function data is unavailable.

        Returns True if successful (mutants killed/survived normally), False on runner crash.
        """
        pyproject_path = Path("pyproject.toml")
        original_pyproject = None
        if pyproject_path.exists():
            original_pyproject = pyproject_path.read_text("utf-8")

        try:
            mutants_to_run, filter_active = self._filter_mutants_by_category(
                paths, relevant_categories, telemetry,
                per_function_categories=per_function_categories,
            )

            self._scope_pyproject_paths(pyproject_path, original_pyproject, paths)

            cmd = self._build_mutmut_command(filter_active, mutants_to_run)
            if cmd is None:
                # Everything was filtered out; nothing to run.
                return True

            return self._run_mutmut_subprocess(
                cmd, telemetry, filter_active, mutants_to_run
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            return False
        finally:
            if original_pyproject is not None:
                pyproject_path.write_text(original_pyproject, "utf-8")

    def _filter_mutants_by_category(
        self,
        paths: list[str],
        relevant_categories: set[MutationOperatorCategory] | None,
        telemetry: MutationTelemetry | None,
        per_function_categories: dict[str, set[MutationOperatorCategory]] | None = None,
    ) -> tuple[list[str], bool]:
        """Pre-execution filtering: select mutants whose category is relevant.

        When *per_function_categories* is provided (Phase 4), each mutant is
        checked against the category set of the specific function it belongs to.
        Falls back to file-level *relevant_categories* when per-function data
        is unavailable or when the mutant's function can't be identified.

        Returns (mutants_to_run, filter_active).
        """
        if relevant_categories is None or not cst:
            return [], False

        mutants_to_run: list[str] = []
        for path in paths:
            try:
                source = Path(path).read_text("utf-8")
                cat_map = self._build_mutant_category_map(path, source)
                for mutant_id, cat in cat_map.items():
                    # Phase 4: Per-function filtering when available
                    if per_function_categories is not None:
                        func_name = _extract_func_name_from_mutant_id(mutant_id)
                        if func_name and func_name in per_function_categories:
                            if cat in per_function_categories[func_name]:
                                mutants_to_run.append(mutant_id)
                            elif telemetry:
                                telemetry.mutants_skipped_policy += 1
                            continue
                    # Fallback: file-level category check
                    if cat in relevant_categories:
                        mutants_to_run.append(mutant_id)
                    elif telemetry:
                        telemetry.mutants_skipped_policy += 1
            except Exception as e:
                logger.debug(f"Failed to generate explicit mutant list for {path}: {e}")
                return [], False

        return mutants_to_run, True

    @staticmethod
    def _scope_pyproject_paths(
        pyproject_path: Path,
        original_pyproject: str | None,
        paths: list[str],
    ) -> None:
        """Rewrite [tool.mutmut] paths_to_mutate to scope to target files."""
        if not original_pyproject or not paths:
            return
        scoped_paths = json.dumps(paths)
        new_content = _re.sub(
            r"(paths_to_mutate\s*=\s*)\[[^\]]*\]",
            f"paths_to_mutate = {scoped_paths}",
            original_pyproject,
        )
        pyproject_path.write_text(new_content, "utf-8")

    def _build_mutmut_command(
        self,
        filter_active: bool,
        mutants_to_run: list[str],
    ) -> list[str] | None:
        """Build the mutmut CLI command.

        Returns None when all mutants have been filtered out (nothing to run).
        """
        max_children = min(self.budget.max_workers, 2)
        cmd = ["mutmut", "run", "--max-children", str(max_children)]

        if filter_active:
            if not mutants_to_run:
                return None
            cmd.extend(mutants_to_run)

        return cmd

    @staticmethod
    def _run_mutmut_subprocess(
        cmd: list[str],
        telemetry: MutationTelemetry | None,
        filter_active: bool,
        mutants_to_run: list[str],
    ) -> bool:
        """Run mutmut subprocess and interpret exit code."""
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=300
        )
        # mutmut v3: 0 = all killed, 2 = survivors found, 1 = other
        if proc.returncode not in (0, 1, 2):
            return False

        if telemetry and filter_active:
            telemetry.mutants_executed += len(mutants_to_run)
        return True

    def _compute_relevant_categories(
        self,
        file_path: str,
        func_name: str,
        algebra_manifest: PropertyManifest | None = None,
        teff_manifest: TestEffectivenessManifest | None = None,
    ) -> set[MutationOperatorCategory]:
        """Layer 1: Exclusionary filtering."""
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
        if teff_manifest:
            # Find file in teff manifest
            # This is a bit simplified: map strong assertions to categories
            # For now, we'll leave it empty until we have a proper mapper
            pass

        return self.relevance_matrix.get_prioritized_categories(
            is_pure=is_pure,
            branch_count=branch_count,
            has_strings=has_strings,
            has_numbers=has_numbers,
            covered_categories=covered_categories,
        )

    def _parse_mutmut_results(
        self,
        paths: list[str],
        project_root: str | None = None,
    ) -> dict[str, FunctionMutationState]:
        """Parse mutmut v3 results and return per-function state.

        mutmut v3 output format:
            module.x__funcname__mutmut_N: killed|survived|timeout|not checked

        We demangle the function name, aggregate per-function, and filter to
        only the requested paths.

        When *project_root* is provided, keys use canonical identity
        (``relpath::qualname``) matching the manifest convention.
        """
        output = self._fetch_mutmut_output()
        if output is None:
            return {}

        mutant_category_map = self._collect_category_maps(paths)
        func_counts = self._aggregate_func_counts(output, mutant_category_map)
        return self._build_function_states(func_counts, paths, project_root)

    @staticmethod
    def _fetch_mutmut_output() -> str | None:
        """Run ``mutmut results`` and return stdout, or None on failure."""
        cmd = ["mutmut", "results", "--all", "true"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                return None
            return proc.stdout
        except (subprocess.SubprocessError, FileNotFoundError):
            return None

    def _collect_category_maps(self, paths: list[str]) -> dict[str, str]:
        """Build a merged mutant-id -> category map for all requested paths."""
        mutant_category_map: dict[str, str] = {}
        for path in paths:
            if not os.path.exists(path):
                continue
            try:
                source = Path(path).read_text("utf-8")
                mutant_category_map.update(
                    self._build_mutant_category_map(path, source)
                )
            except Exception as e:
                logger.debug(f"Failed to build category map for {path}: {e}")
        return mutant_category_map

    @staticmethod
    def _aggregate_func_counts(
        output: str, mutant_category_map: dict[str, str]
    ) -> dict[str, dict[str, Any]]:
        """Parse mutmut v3 output lines and aggregate counts per mangled function."""
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

            func_mangled = _re.sub(r"__mutmut_\d+$", "", name_part)

            if func_mangled not in func_counts:
                func_counts[func_mangled] = {
                    "killed": 0,
                    "survived": 0,
                    "timeout": 0,
                    "total": 0,
                    "survived_by_category": {},
                }

            entry = func_counts[func_mangled]
            entry["total"] += 1
            _tally_status(entry, status, name_part, mutant_category_map)

        return func_counts

    @staticmethod
    def _build_function_states(
        func_counts: dict[str, dict[str, Any]],
        paths: list[str],
        project_root: str | None = None,
    ) -> dict[str, FunctionMutationState]:
        """Demangle aggregated counts, filter to requested paths, and build states.

        When *project_root* is provided, keys use :func:`canonicalize_function_id`
        so that they match the manifest's ``relpath::qualname`` convention.
        """
        from lintgate.mutation.state import canonicalize_function_id

        states: dict[str, FunctionMutationState] = {}

        for mangled, counts in func_counts.items():
            if counts["total"] == 0:
                continue

            file_path, func_name = _demangle_mutmut_name(mangled)
            if not file_path or not func_name:
                continue

            matched_path = _match_path(file_path, paths)
            if matched_path is None:
                continue

            code_content = ""
            if os.path.exists(matched_path):
                with contextlib.suppress(OSError):
                    code_content = Path(matched_path).read_text("utf-8")

            if project_root is not None:
                func_id = canonicalize_function_id(
                    matched_path, func_name, project_root
                )
            else:
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
                            (s[1] for s in reversed(self._stack) if s[0] == "class"),
                            None,
                        )
                        mangled = mangle_function_name(
                            name=node.name.value, class_name=class_name
                        )
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
                                        cat = self._map_op_name_to_category(
                                            op_name, node
                                        )
                                        for _ in inventions:
                                            self.mutants.append((current_func, cat))
                                except Exception:
                                    continue
                    return True

                def on_leave(self, node: cst.CSTNode):
                    if (
                        isinstance(node, (cst.ClassDef, cst.FunctionDef))
                        and self._stack
                    ):
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
                            (
                                cst.Plus,
                                cst.Minus,
                                cst.Add,
                                cst.Subtract,
                                cst.Multiply,
                                cst.Divide,
                            ),
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

    def _build_mutant_category_map_with_ast(
        self, module_name: str, source: str
    ) -> dict[str, str]:
        """Approximate mutmut category IDs using plain AST traversal."""

        class_sep = "\u01c1"

        def _is_string_expr(node: ast.AST) -> bool:
            return isinstance(node, ast.JoinedStr) or (
                isinstance(node, ast.Constant) and isinstance(node.value, str)
            )

        def _infer_category(node: ast.AST) -> str | None:
            if isinstance(node, ast.BinOp) and isinstance(
                node.op,
                (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow),
            ):
                if _is_string_expr(node.left) or _is_string_expr(node.right):
                    return "string"
                return "arithmetic"
            if isinstance(node, ast.BoolOp):
                return "conditional"
            if isinstance(node, ast.Compare):
                return "conditional"
            if isinstance(
                node, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.Match)
            ):
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
                    child,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
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

            def _visit_function(
                self, node: ast.FunctionDef | ast.AsyncFunctionDef
            ) -> None:
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
            logger.debug(
                f"Exception in _build_mutant_category_map_with_ast for {module_name}: {e}"
            )
            return {}


def _extract_func_name_from_mutant_id(mutant_id: str) -> str | None:
    """Extract the simple function name from a mutmut mutant ID.

    Mutant IDs follow the format ``{module}.{base}__mutmut_{n}`` where
    ``base`` is one of:
    - ``x_funcname`` for module-level functions
    - ``x\\u01c1ClassName\\u01c1method_name`` for class methods

    Returns the simple function/method name (matching AST ``node.name``),
    or ``None`` if the ID cannot be parsed.

    Examples::

        >>> _extract_func_name_from_mutant_id("mod.sub.x_compute__mutmut_3")
        'compute'
        >>> _extract_func_name_from_mutant_id("mod.sub.x\\u01c1Cls\\u01c1run__mutmut_1")
        'run'
        >>> _extract_func_name_from_mutant_id("mod.sub.x__private__mutmut_2")
        '_private'
    """
    # Strip __mutmut_N suffix
    mutmut_idx = mutant_id.find("__mutmut_")
    if mutmut_idx == -1:
        return None

    prefix = mutant_id[:mutmut_idx]

    class_sep = "\u01c1"  # mutmut's CLASS_NAME_SEPARATOR

    # Class method: look for .xǁ pattern
    class_marker = ".x" + class_sep
    class_idx = prefix.rfind(class_marker)
    if class_idx != -1:
        remainder = prefix[class_idx + 2:]  # skip '.x'
        parts = [p for p in remainder.split(class_sep) if p]
        # parts = ['ClassName', 'method_name'] — return the method name
        return parts[-1] if parts else None

    # Module-level: look for .x_ pattern
    func_marker = ".x_"
    func_idx = prefix.rfind(func_marker)
    if func_idx != -1:
        return prefix[func_idx + 3:]  # skip '.x_' → 'funcname'

    return None


def _tally_status(
    entry: dict[str, Any],
    status: str,
    name_part: str,
    mutant_category_map: dict[str, str],
) -> None:
    """Increment the appropriate counter in *entry* based on *status*."""
    if status == "killed":
        entry["killed"] += 1
    elif status == "survived":
        entry["survived"] += 1
        cat = mutant_category_map.get(name_part)
        if cat:
            entry["survived_by_category"][cat] = (
                entry["survived_by_category"].get(cat, 0) + 1
            )
    elif status == "timeout":
        entry["timeout"] += 1


def _match_path(file_path: str, paths: list[str]) -> str | None:
    """Map a demangled *file_path* back to an entry in *paths*.

    Returns the absolute matched path, or None when *paths* is non-empty and
    no match is found.  When *paths* is empty the demangled path is returned
    as-is.
    """
    if not paths:
        return file_path

    for p in paths:
        abs_p = os.path.abspath(p)
        # Use endswith check to handle absolute vs relative or missing leading slash
        if abs_p.endswith(os.path.normpath(file_path)):
            return abs_p

    return None


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

    def visit_Str(self, node: ast.Str):  # Support Python < 3.8
        self.has_strings = True

    def visit_Num(self, node: ast.Num):  # Support Python < 3.8
        self.has_numbers = True

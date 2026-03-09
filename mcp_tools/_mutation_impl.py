"""Mutation tools implementation — helpers, context, and analysis logic.

Extracted from mutation_tools.py to stay under the 400-line file limit.
The public API is mutation_tools.register(); this module is private.
"""

from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_MUTATION_CACHE_DIR = ".lintgate/mutation"


@dataclass
class MutationContext:
    """Bundled context for mutation analysis runs."""

    full_path: str
    rel_path: str
    cache_dir: Path
    purity_map: dict[str, bool] = field(default_factory=dict)
    test_files: list[str] = field(default_factory=list)


# ── File/function resolution ──────────────────────────────────────


def resolve_function(
    project_root: str,
    file: str,
    function: str | None,
) -> tuple[str, ast.FunctionDef | None, str | None]:
    """Resolve file and optionally find a function node.

    Returns (full_path, func_node_or_None, error_or_None).
    """
    full = os.path.join(project_root, file) if not os.path.isabs(file) else file
    if not os.path.isfile(full):
        return full, None, f"File not found: {file}"

    try:
        with open(full, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=full)
    except (OSError, SyntaxError) as e:
        return full, None, f"Parse error: {e}"

    if function:
        if "." in function:
            # Qualified name: Class.method — walk class hierarchy
            parts = function.split(".")
            method_name = parts[-1]
            class_chain = parts[:-1]
            scope: ast.AST = tree
            for class_name in class_chain:
                found = False
                for child in getattr(scope, "body", []):
                    if isinstance(child, ast.ClassDef) and child.name == class_name:
                        scope = child
                        found = True
                        break
                if not found:
                    return full, None, f"Class '{class_name}' not found in {file}"
            for child in getattr(scope, "body", []):
                if (
                    isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name == method_name
                ):
                    return full, child, None
            return full, None, f"Method '{method_name}' not found in class chain in {file}"
        else:
            for node in ast.walk(tree):
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == function
                ):
                    return full, node, None
        return full, None, f"Function '{function}' not found in {file}"

    return full, None, None


def walk_functions(tree: ast.Module) -> list[tuple[str, ast.FunctionDef]]:
    """Walk AST yielding (qualname, node) for each function."""
    results: list[tuple[str, ast.FunctionDef]] = []

    def _walk(scope: ast.AST, prefix: str) -> None:
        for node in getattr(scope, "body", []):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{node.name}" if prefix else node.name
                results.append((name, node))
            elif isinstance(node, ast.ClassDef):
                cp = f"{prefix}{node.name}." if prefix else f"{node.name}."
                _walk(node, cp)

    _walk(tree, "")
    return results


def parse_file(full_path: str) -> ast.Module | None:
    """Parse a Python file, returning None on error."""
    try:
        with open(full_path, encoding="utf-8") as fh:
            return ast.parse(fh.read(), filename=full_path)
    except (OSError, SyntaxError):
        return None


# ── Cache helpers ─────────────────────────────────────────────────


def get_cache_dir(project_root: str) -> Path:
    return Path(project_root) / _MUTATION_CACHE_DIR


def load_cached_state(cache_dir: Path, func_key: str) -> dict | None:
    safe_key = func_key.replace("::", "__").replace("/", "_")
    cache_file = cache_dir / f"{safe_key}.json"
    if not cache_file.exists():
        return None
    try:
        with open(cache_file, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save_cached_state(cache_dir: Path, func_key: str, data: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_key = func_key.replace("::", "__").replace("/", "_")
    cache_file = cache_dir / f"{safe_key}.json"
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def iter_cached_states(
    cache_dir: Path,
    file: str | None = None,
    function: str | None = None,
) -> list[dict]:
    """Load all cached mutation states, optionally filtered by file/function."""
    if not cache_dir.exists():
        return []
    states: list[dict] = []
    for cache_file in sorted(cache_dir.glob("*.json")):
        if cache_file.name == "scheduler_state.json":
            continue
        try:
            with open(cache_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        func_key = data.get("function_key", "")
        if file and file not in func_key:
            continue
        if function and function not in func_key:
            continue
        states.append(data)
    return states


# ── Test discovery + loading ──────────────────────────────────────


def discover_test_files(project_root: str, source_file: str) -> list[str]:
    """Find test files relevant to a source file.

    Uses multiple strategies:
    1. Exact match: test_{basename}.py
    2. Prefix match: test_{basename_prefix}*.py (handles files like
       perf001_quadratic_membership.py → test_perf*.py won't match,
       but the parent package name often does)
    3. Package-aware: for files in subpackages like performance_checks/,
       also search for test_{package_name}*.py
    """
    base = os.path.splitext(os.path.basename(source_file))[0]
    test_dirs = ["tests", "test"]

    # Build candidate patterns: exact match + parent package match
    exact_candidates = [f"test_{base}.py", f"{base}_test.py"]

    # For files in subpackages, also try the parent package name
    # e.g., lintgate/linters/performance_checks/perf001.py → test_performance_checks*.py
    parent_dir = os.path.basename(os.path.dirname(source_file))
    package_candidates = []
    if parent_dir and parent_dir not in ("lintgate", "src", "lib"):
        package_candidates.append(f"test_{parent_dir}")

    found: list[str] = []
    seen: set[str] = set()
    for td in test_dirs:
        test_dir = os.path.join(project_root, td)
        if not os.path.isdir(test_dir):
            continue
        # Search top-level and subdirectories (e.g. tests/generated/)
        search_dirs = [test_dir]
        try:
            for entry in os.listdir(test_dir):
                sub = os.path.join(test_dir, entry)
                if os.path.isdir(sub) and not entry.startswith("."):
                    search_dirs.append(sub)
        except OSError:
            pass

        for sdir in search_dirs:
            # Exact matches
            for cand in exact_candidates:
                full = os.path.join(sdir, cand)
                if os.path.isfile(full) and full not in seen:
                    found.append(full)
                    seen.add(full)
            # Package-name prefix matches
            for prefix in package_candidates:
                try:
                    for entry in os.listdir(sdir):
                        if entry.startswith(prefix) and entry.endswith(".py"):
                            full = os.path.join(sdir, entry)
                            if full not in seen:
                                found.append(full)
                                seen.add(full)
                except OSError:
                    continue
            # Also match characterization tests: test_char_{base}.py
            char_cand = f"test_char_{base}.py"
            char_full = os.path.join(sdir, char_cand)
            if os.path.isfile(char_full) and char_full not in seen:
                found.append(char_full)
                seen.add(char_full)
    return found


def load_test_callables(test_files: list[str], func_name: str) -> list[Any]:
    """Discover and import test callables for a function via test-impact map.

    Falls back to loading all test functions from the discovered test files
    when the AST-based impact map finds no direct references to func_name.
    This handles indirect calls (fixtures, parametrize, helper wrappers)
    that static name matching misses.
    """
    from lintgate.specification.test_impact import build_test_impact_map

    impact = build_test_impact_map(test_files)
    refs = impact.tests_for(func_name)
    if refs:
        return _import_test_functions(refs)

    # Fallback: load all test functions from the relevant test files.
    # The test files were already scoped by filename convention in
    # discover_test_files, so this is bounded and relevant.
    return _load_all_tests_from_files(test_files)


def _load_all_tests_from_files(test_files: list[str]) -> list[Any]:
    """Import all test_ functions from the given test files.

    Handles both module-level test functions and class-based test methods
    (e.g., class TestFoo with test_bar methods). For class methods, yields
    bound methods on fresh instances so they can be called zero-arg.
    """
    callables: list[Any] = []
    for tf in test_files:
        mod = _try_import_module(tf)
        if mod is None:
            continue
        for name in dir(mod):
            obj = getattr(mod, name, None)
            if obj is None:
                continue
            # Module-level test functions
            if name.startswith("test_") and callable(obj):
                callables.append(obj)
            # Test classes: extract test methods as bound methods on instances
            elif isinstance(obj, type) and name.startswith("Test"):
                try:
                    instance = obj()
                except Exception:
                    continue
                for method_name in dir(obj):
                    if method_name.startswith("test_"):
                        method = getattr(instance, method_name, None)
                        if callable(method):
                            callables.append(method)
    return callables


def _import_test_functions(refs: list[Any]) -> list[Any]:
    """Import test functions from TestReference objects.

    Handles both module-level test functions and class-based test methods.
    For class methods, searches Test* classes for the method name and yields
    a bound method on a fresh instance.
    """
    callables: list[Any] = []
    loaded_modules: dict[str, Any] = {}
    for ref in refs:
        if ref.test_file not in loaded_modules:
            loaded_modules[ref.test_file] = _try_import_module(ref.test_file)
        mod = loaded_modules[ref.test_file]
        if mod is None:
            continue
        # Try module-level first
        test_fn = getattr(mod, ref.test_function, None)
        if test_fn is not None and callable(test_fn):
            callables.append(test_fn)
            continue
        # Search Test* classes for the method
        found = _find_method_in_test_classes(mod, ref.test_function)
        if found is not None:
            callables.append(found)
    return callables


def _find_method_in_test_classes(mod: Any, method_name: str) -> Any:
    """Search Test* classes in a module for a method by name.

    Returns a bound method on a fresh instance, or None.
    """
    for name in dir(mod):
        if not name.startswith("Test"):
            continue
        cls = getattr(mod, name, None)
        if not isinstance(cls, type):
            continue
        if hasattr(cls, method_name):
            try:
                instance = cls()
                method = getattr(instance, method_name, None)
                if callable(method):
                    return method
            except Exception:
                continue
    return None


def _try_import_module(filepath: str) -> Any:
    """Try to dynamically import a Python file as a module.

    Establishes project import context by adding the project root
    (directory containing tests/) to sys.path before import.
    """
    import importlib.util
    import sys

    # Find project root: walk up from filepath to find a directory containing
    # a tests/ dir or pyproject.toml, ensuring package imports resolve.
    project_root = _infer_project_root(filepath)
    path_added = False
    if project_root and project_root not in sys.path:
        sys.path.insert(0, project_root)
        path_added = True

    mod_name = f"_mutation_test_{os.path.basename(filepath).replace('.py', '')}"
    spec = importlib.util.spec_from_file_location(mod_name, filepath)
    if spec is None or spec.loader is None:
        if path_added:
            sys.path.remove(project_root)
        return None
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None
    finally:
        # Leave project_root in sys.path — other test modules may need it
        pass


def _infer_project_root(filepath: str) -> str | None:
    """Walk up from filepath to find project root (contains pyproject.toml or tests/)."""
    current = os.path.dirname(os.path.abspath(filepath))
    for _ in range(10):  # max 10 levels up
        if os.path.exists(os.path.join(current, "pyproject.toml")):
            return current
        if os.path.isdir(os.path.join(current, "tests")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


# ── Purity detection ──────────────────────────────────────────────


def detect_purity(full_path: str, func_name: str) -> bool:
    """Fast purity check for a single function using the algebra pipeline."""
    try:
        from lintgate.linters.performance_checks.purity import analyze_purity

        tree = parse_file(full_path)
        if tree is None:
            return False
        purity_map = analyze_purity(tree)
        for key, result in purity_map.items():
            if key == func_name or key.endswith(f".{func_name}"):
                return result.is_pure
    except Exception:
        pass
    return False


def detect_purity_map(full_path: str) -> dict[str, bool]:
    """Build function_name -> is_pure map for all functions in a file."""
    try:
        from lintgate.linters.performance_checks.purity import analyze_purity

        tree = parse_file(full_path)
        if tree is None:
            return {}
        purity_map = analyze_purity(tree)
        return {k: v.is_pure for k, v in purity_map.items()}
    except Exception:
        return {}


def lookup_purity(purity_map: dict[str, bool], func_name: str) -> bool:
    """Look up purity for a function, trying both bare and qualified names."""
    if func_name in purity_map:
        return purity_map[func_name]
    bare = func_name.split(".")[-1]
    for key, is_pure in purity_map.items():
        if key == bare or key.endswith(f".{bare}"):
            return is_pure
    return False


# ── Mutation execution ────────────────────────────────────────────


def run_on_functions_with_tests(
    ctx: MutationContext,
    func_node: Any,
    function: str | None,
    runner: Any,
    filter_fn: Any,
    key_fn: Any,
) -> list[dict] | str:
    """Run mutation analysis with real test selection and purity-aware filtering."""
    results: list[dict] = []
    if func_node and function:
        results.append(_run_single(ctx, func_node, function, runner, filter_fn, key_fn))
    else:
        tree = parse_file(ctx.full_path)
        if tree is None:
            return json.dumps({"error": f"Parse error: {ctx.full_path}"})
        for qualname, node in walk_functions(tree):
            results.append(_run_single(ctx, node, qualname, runner, filter_fn, key_fn))
    return results


def _run_single(
    ctx: MutationContext,
    node: Any,
    func_name: str,
    runner: Any,
    filter_fn: Any,
    key_fn: Any,
) -> dict:
    """Run mutation analysis on a single function with tests and purity."""
    is_pure = lookup_purity(ctx.purity_map, func_name)
    cats = filter_fn(node, is_pure=is_pure)
    func_key = key_fn(ctx.rel_path, func_name)
    bare_name = func_name.split(".")[-1]
    tests = load_test_callables(ctx.test_files, bare_name)
    sr = runner(node, func_key, cats, tests, lambda *a: None)
    result_dict = sr.to_dict()
    result_dict["tests_loaded"] = len(tests)
    result_dict["is_pure"] = is_pure
    result_dict["parameter_count"] = len(getattr(node, "args", _EMPTY_ARGS).args)
    save_cached_state(ctx.cache_dir, func_key, result_dict)
    return result_dict


class _EmptyArgs:
    args: list = []  # noqa: RUF012


_EMPTY_ARGS = _EmptyArgs()


# ── Post-profiling analysis ───────────────────────────────────────


def run_post_profiling_analysis(
    results: list[dict],
    purity_map: dict[str, bool],
) -> dict:
    """Run convergence and symmetry analysis on profiling results.

    sigma is approximated by total_mutants (the empirical mutation population).
    This is an upper bound on true σ — convergence/symmetry analysis uses it
    to measure kill coverage against the generated mutant set.
    """
    from lintgate.specification.greedy_convergence import analyze_convergence
    from lintgate.specification.symmetry_classifier import classify_regime_from_mutations

    convergence_results: list[dict] = []
    symmetry_results: list[dict] = []

    for result_dict in results:
        pr = reconstruct_profiling_result(result_dict)
        if pr is None:
            continue

        # total_mutants as empirical sigma (upper bound on true σ)
        sigma = result_dict.get("total_mutants", 0)
        is_pure = result_dict.get("is_pure", False)
        param_count = result_dict.get("parameter_count", 0)

        conv = analyze_convergence(pr, sigma)
        convergence_results.append(conv.to_dict())

        sym = classify_regime_from_mutations(pr, sigma, is_pure, param_count)
        symmetry_results.append(sym.to_dict())

    return {"convergence": convergence_results, "symmetry": symmetry_results}


def reconstruct_profiling_result(result_dict: dict) -> Any:
    """Reconstruct a ProfilingResult from cached dict for analysis."""
    from lintgate.specification.mutation_engine import (
        CategoryResult,
        MutationCategory,
        ProfilingResult,
    )

    if result_dict.get("coverage_depth") != "profiled":
        return None

    per_cat = []
    for cd in result_dict.get("per_category", []):
        try:
            cat = MutationCategory(cd["category"])
        except (ValueError, KeyError):
            continue
        per_cat.append(
            CategoryResult(
                category=cat,
                total=cd.get("total", 0),
                killed=cd.get("killed", 0),
                survived=cd.get("survived", 0),
                killed_by_assertion=cd.get("killed_by_assertion", 0),
                killed_by_crash=cd.get("killed_by_crash", 0),
            )
        )

    return ProfilingResult(
        function_key=result_dict.get("function_key", ""),
        categories_tested=result_dict.get("categories_tested", 0),
        total_mutants=result_dict.get("total_mutants", 0),
        total_killed=result_dict.get("total_killed", 0),
        total_survived=result_dict.get("total_survived", 0),
        survival_rate=result_dict.get("survival_rate", 0.0),
        per_category=per_cat,
        kill_matrix=result_dict.get("kill_matrix", {}),
    )


# ── Prescription helpers ──────────────────────────────────────────


def prescription_for_category(category: str) -> str:
    """Map surviving category to actionable prescription."""
    prescriptions = {
        "VALUE": "Add exact-value assertions: assert f(input) == expected_output",
        "SWAP": "Add parameter-order tests: verify f(a, b) != f(b, a) where applicable",
        "BOUNDARY": "Add boundary-value tests: test at boundary-1, boundary, boundary+1",
        "STATE": "Add state-verification tests: check self.attr after method calls",
        "TYPE": "Add type-discrimination tests: verify isinstance checks affect behavior",
    }
    return prescriptions.get(category, f"Add tests targeting {category} mutations")


def generate_test_skeleton(func_key: str, category: str) -> dict:
    """Generate a pytest test skeleton for a surviving category."""
    short_name = func_key.split("::")[-1] if "::" in func_key else func_key
    safe_name = short_name.replace(".", "_")
    test_name = f"test_{safe_name}_{category.lower()}_mutation"

    # Detect methods (Class.method) vs bare functions
    if "." in short_name:
        parts = short_name.split(".")
        class_name = parts[0]
        method_name = parts[-1]
        templates = {
            "VALUE": f"def {test_name}():\n    obj = {class_name}()\n    result = obj.{method_name}(...)\n    assert result == EXPECTED_VALUE\n",
            "SWAP": f"def {test_name}():\n    obj = {class_name}()\n    assert obj.{method_name}(a, b) != obj.{method_name}(b, a)\n",
            "BOUNDARY": f"def {test_name}():\n    obj = {class_name}()\n    assert obj.{method_name}(boundary - 1) == BELOW_RESULT\n    assert obj.{method_name}(boundary + 1) == ABOVE_RESULT\n",
            "STATE": f"def {test_name}():\n    obj = {class_name}()\n    obj.{method_name}(input_val)\n    assert obj.state_attr == EXPECTED_STATE\n",
            "TYPE": f"def {test_name}():\n    obj = {class_name}()\n    assert obj.{method_name}(valid_type) != obj.{method_name}(invalid_type)\n",
        }
    else:
        templates = {
            "VALUE": f"def {test_name}():\n    result = {short_name}(...)\n    assert result == EXPECTED_VALUE\n",
            "SWAP": f"def {test_name}():\n    assert {short_name}(a, b) != {short_name}(b, a)\n",
            "BOUNDARY": f"def {test_name}():\n    assert {short_name}(boundary - 1) == BELOW_RESULT\n    assert {short_name}(boundary + 1) == ABOVE_RESULT\n",
            "STATE": f"def {test_name}():\n    result = {short_name}(input_val)\n    assert result == EXPECTED_STATE\n",
            "TYPE": f"def {test_name}():\n    assert {short_name}(valid_type) != {short_name}(invalid_type)\n",
        }
    return {
        "function": func_key,
        "category": category,
        "test_name": test_name,
        "skeleton": templates.get(category, f"def {test_name}(): pass"),
    }

"""Colab notebook generation — offload mutation sweeps to free cloud compute.

Generates a self-contained .ipynb notebook pre-configured with the project's
repo URL, branch, and cached profile count. The notebook runs the full
mutation sweep on Colab and produces a downloadable zip that slots directly
into .lintgate/mutation/ locally.

Called by the `colab_sweep_generate` MCP tool in convergence_tools.py.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


def _get_git_info(project_root: str) -> dict[str, str]:
    """Extract repo URL and branch from git."""
    info: dict[str, str] = {"repo_url": "", "branch": "main"}
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            cwd=project_root,
            timeout=5,
        )
        if result.returncode == 0:
            info["repo_url"] = result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=project_root,
            timeout=5,
        )
        if result.returncode == 0:
            info["branch"] = result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return info


def _count_cached_profiles(project_root: str) -> int:
    """Count existing mutation profiles in cache."""
    cache_dir = Path(project_root) / ".lintgate" / "mutation"
    if not cache_dir.exists():
        return 0
    return len([f for f in cache_dir.iterdir() if f.suffix == ".json"])


def _count_source_files(project_root: str) -> int:
    """Count profiling-eligible source files."""
    count = 0
    for subdir in ("lintgate", "mcp_tools"):
        base = os.path.join(project_root, subdir)
        if not os.path.isdir(base):
            continue
        for _dirpath, _, filenames in os.walk(base):
            for fn in filenames:
                if fn.endswith(".py") and not fn.startswith("test_") and not fn.endswith("_test.py"):
                    count += 1
    return count


def _build_notebook(
    repo_url: str,
    branch: str,
    workers: int,
    budget_ms: int,
    cached_count: int,
    source_count: int,
    local_project_path: str,
) -> dict[str, Any]:
    """Build the .ipynb notebook structure."""

    def _md(source: str) -> dict[str, Any]:
        return {
            "cell_type": "markdown",
            "metadata": {},
            "source": [line + "\n" for line in source.split("\n")],
        }

    def _code(source: str) -> dict[str, Any]:
        return {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": [line + "\n" for line in source.split("\n")],
        }

    # ── Embedded sweep logic (no external script dependency) ──
    sweep_code = (
        "import ast, os, sys, time\n"
        "from concurrent.futures import ProcessPoolExecutor, as_completed\n"
        "from pathlib import Path\n"
        "\n"
        f"WORKERS = {workers}\n"
        f"BUDGET_MS = {budget_ms}.0\n"
        "SKIP_CACHED = True\n"
        "\n"
        "def collect_python_files(root):\n"
        "    files = []\n"
        "    for subdir in ('lintgate', 'mcp_tools'):\n"
        "        base = os.path.join(root, subdir)\n"
        "        if not os.path.isdir(base):\n"
        "            continue\n"
        "        for dirpath, _, filenames in os.walk(base):\n"
        "            for fn in sorted(filenames):\n"
        "                if fn.endswith('.py') and not fn.startswith('test_') and not fn.endswith('_test.py'):\n"
        "                    files.append(os.path.relpath(os.path.join(dirpath, fn), root))\n"
        "    return files\n"
        "\n"
        "def profile_single_file(args):\n"
        "    project_root, rel_path, budget_ms, skip_cached = args\n"
        "    import ast, os, sys, time\n"
        "    sys.path.insert(0, project_root)\n"
        "    from lintgate.specification.mutation_engine import run_function_sampling\n"
        "    from lintgate.keys import canonical_function_key\n"
        "    from lintgate.specification.mutation_filter import filter_categories\n"
        "    from mcp_tools._mutation_impl import (\n"
        "        MutationContext, detect_purity_map, discover_test_files,\n"
        "        get_cache_dir, load_test_callables, lookup_purity,\n"
        "        parse_file, save_cached_state, walk_functions,\n"
        "    )\n"
        "    full_path = os.path.join(project_root, rel_path)\n"
        "    cache_dir = get_cache_dir(project_root)\n"
        "    start = time.monotonic()\n"
        "    result = {'file': rel_path, 'profiled': 0, 'cached': 0, 'trivial': 0, 'errors': 0}\n"
        "    tree = parse_file(full_path)\n"
        "    if tree is None:\n"
        "        result['errors'] = 1\n"
        "        return result\n"
        "    functions = walk_functions(tree)\n"
        "    if not functions:\n"
        "        return result\n"
        "    test_files = discover_test_files(project_root, full_path)\n"
        "    purity_map = detect_purity_map(full_path)\n"
        "    ctx = MutationContext(\n"
        "        full_path=full_path, rel_path=rel_path, cache_dir=cache_dir,\n"
        "        purity_map=purity_map, test_files=test_files, project_root=project_root,\n"
        "    )\n"
        "    for qualname, node in functions:\n"
        "        func_key = canonical_function_key(rel_path, qualname)\n"
        "        if skip_cached:\n"
        "            safe_key = func_key.replace('::', '__').replace('/', '_')\n"
        "            if (cache_dir / f'{safe_key}.json').exists():\n"
        "                result['cached'] += 1\n"
        "                continue\n"
        "        body = getattr(node, 'body', [])\n"
        "        if len(body) <= 1:\n"
        "            stmt = body[0] if body else None\n"
        "            if isinstance(stmt, (ast.Return, ast.Expr)):\n"
        "                result['trivial'] += 1\n"
        "                continue\n"
        "        is_pure = lookup_purity(purity_map, qualname)\n"
        "        cats = filter_categories(node, is_pure=is_pure)\n"
        "        bare_name = qualname.split('.')[-1]\n"
        "        tests, _ = load_test_callables(\n"
        "            ctx.test_files, bare_name,\n"
        "            project_root=ctx.project_root, func_key=func_key,\n"
        "        )\n"
        "        try:\n"
        "            sr = run_function_sampling(\n"
        "                node, func_key, cats, tests, lambda *_: None, budget_ms=budget_ms,\n"
        "            )\n"
        "            rd = sr.to_dict()\n"
        "            rd['tests_loaded'] = len(tests)\n"
        "            rd['is_pure'] = is_pure\n"
        "            args_node = getattr(node, 'args', None)\n"
        "            rd['parameter_count'] = len(args_node.args) if args_node else 0\n"
        "            save_cached_state(ctx.cache_dir, func_key, rd)\n"
        "            result['profiled'] += 1\n"
        "        except Exception:\n"
        "            result['errors'] += 1\n"
        "    result['elapsed_s'] = round(time.monotonic() - start, 2)\n"
        "    return result\n"
        "\n"
        "project_root = os.path.abspath(PROJECT_DIR)\n"
        "files = collect_python_files(project_root)\n"
        "print(f'Found {len(files)} source files | Workers: {WORKERS} | Budget: {BUDGET_MS}ms')\n"
        "cache_dir = Path(project_root) / '.lintgate' / 'mutation'\n"
        "cache_dir.mkdir(parents=True, exist_ok=True)\n"
        "work = [(project_root, f, BUDGET_MS, SKIP_CACHED) for f in files]\n"
        "totals = {'profiled': 0, 'cached': 0, 'trivial': 0, 'errors': 0}\n"
        "start = time.monotonic()\n"
        "with ProcessPoolExecutor(max_workers=WORKERS) as pool:\n"
        "    futures = {pool.submit(profile_single_file, w): w[1] for w in work}\n"
        "    for i, future in enumerate(as_completed(futures), 1):\n"
        "        rel = futures[future]\n"
        "        try:\n"
        "            r = future.result()\n"
        "            for k in totals:\n"
        "                totals[k] += r.get(k, 0)\n"
        "            print(f'[{i}/{len(files)}] {rel}: {r.get(\"profiled\",0)} profiled, '\n"
        "                  f'{r.get(\"cached\",0)} cached, {r.get(\"elapsed_s\",\"?\")}s')\n"
        "        except Exception as e:\n"
        "            print(f'[{i}/{len(files)}] {rel}: FAILED - {e}')\n"
        "            totals['errors'] += 1\n"
        "elapsed = round(time.monotonic() - start, 1)\n"
        "print(f'\\n{\"=\"*60}')\n"
        "print(f'Done in {elapsed}s')\n"
        "print(f'  Profiled: {totals[\"profiled\"]}')\n"
        "print(f'  Cached:   {totals[\"cached\"]}')\n"
        "print(f'  Trivial:  {totals[\"trivial\"]}')\n"
        "print(f'  Errors:   {totals[\"errors\"]}')"
    )

    cells = [
        _md(
            "# LintGate Mutation Sweep\n"
            "\n"
            f"**Auto-generated** for `{branch}` branch. Fully self-contained.\n"
            "\n"
            f"- Source files to profile: **{source_count}**\n"
            f"- Already cached locally: **{cached_count}**\n"
            f"- Estimated new profiles: **~{max(0, source_count - cached_count)}**\n"
            "\n"
            "**How to use:** Runtime > Run all. Wait. Download the zip at the end.\n"
            f"Then: `cd {local_project_path} && unzip ~/Downloads/mutation_results.zip`"
        ),
        _md("## Step 1: Clone & install"),
        _code(
            "import os, shutil, subprocess, sys\n"
            "\n"
            f'REPO_URL = "{repo_url}"\n'
            f'BRANCH = "{branch}"\n'
            'PROJECT_DIR = "/content/lintgate"\n'
            "\n"
            "# Uncomment if repo is private:\n"
            '# GITHUB_TOKEN = "ghp_xxxxxxxxxxxxxxxxxxxx"\n'
            "\n"
            "if os.path.exists(PROJECT_DIR):\n"
            "    shutil.rmtree(PROJECT_DIR)\n"
            "\n"
            "clone_url = REPO_URL\n"
            "try:\n"
            "    GITHUB_TOKEN\n"
            '    clone_url = REPO_URL.replace("https://", f"https://{GITHUB_TOKEN}@")\n'
            "    print('Using authenticated clone')\n"
            "except NameError:\n"
            "    print('Using public clone')\n"
            "\n"
            "result = subprocess.run(\n"
            "    ['git', 'clone', '--depth', '1', '--branch', BRANCH, clone_url, PROJECT_DIR],\n"
            "    capture_output=True, text=True\n"
            ")\n"
            "if result.returncode != 0:\n"
            "    print(f'ERROR: git clone failed!\\n{result.stderr}')\n"
            "    print(f'Common fixes:')\n"
            "    print(f'  - Branch not on remote? Try BRANCH = \"main\"')\n"
            "    print(f'  - Private repo? Set GITHUB_TOKEN above')\n"
            "    raise RuntimeError('Clone failed')\n"
            "print(f'Cloned {BRANCH} to {PROJECT_DIR}')\n"
            "\n"
            "subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'pyyaml', 'packaging'], check=True)\n"
            "r = subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-e', PROJECT_DIR],\n"
            "                   capture_output=True, text=True)\n"
            "if r.returncode != 0:\n"
            "    print(f'pip install -e failed, using sys.path fallback')\n"
            "    sys.path.insert(0, PROJECT_DIR)\n"
            "else:\n"
            "    for m in list(sys.modules):\n"
            "        if m.startswith('lintgate'):\n"
            "            del sys.modules[m]\n"
            "\n"
            "try:\n"
            "    from lintgate.specification.mutation_engine import MutationCategory\n"
            "except ImportError:\n"
            "    sys.path.insert(0, PROJECT_DIR)\n"
            "    from lintgate.specification.mutation_engine import MutationCategory\n"
            "print(f'Import OK. Categories: {[c.value for c in MutationCategory]}')\n"
            "print('Ready for Step 2!')"
        ),
        _md("## Step 2: Run sweep"),
        _code(sweep_code),
        _md("## Step 3: Review & download"),
        _code(
            "import json, os\n"
            "from pathlib import Path\n"
            "\n"
            "cache_dir = Path(PROJECT_DIR) / '.lintgate' / 'mutation'\n"
            "files = sorted(cache_dir.glob('*.json')) if cache_dir.exists() else []\n"
            "print(f'Total profiles: {len(files)}')\n"
            "\n"
            "total_k, total_s = 0, 0\n"
            "high = []\n"
            "for f in files:\n"
            "    try:\n"
            "        d = json.loads(f.read_text())\n"
            "    except Exception:\n"
            "        continue\n"
            "    k, s = d.get('total_killed', 0), d.get('total_survived', 0)\n"
            "    total_k += k\n"
            "    total_s += s\n"
            "    r = d.get('survival_rate', 0)\n"
            "    if r > 0.5 and (k + s) > 0:\n"
            "        high.append((d.get('function_key', '?'), r))\n"
            "\n"
            "t = total_k + total_s\n"
            "print(f'Kill rate: {total_k}/{t} ({total_k/t:.1%})' if t else 'No mutants')\n"
            "print(f'High-survival functions: {len(high)}')\n"
            "for key, r in sorted(high, key=lambda x: -x[1])[:15]:\n"
            "    print(f'  {r:.0%} {key}')"
        ),
        _code(
            "import os\n"
            "from pathlib import Path\n"
            "\n"
            "mutation_dir = Path(PROJECT_DIR) / '.lintgate' / 'mutation'\n"
            "mutation_dir.mkdir(parents=True, exist_ok=True)\n"
            "\n"
            "zip_path = '/content/mutation_results.zip'\n"
            "!cd {PROJECT_DIR} && zip -r {zip_path} .lintgate/mutation/ -x '*.DS_Store'\n"
            "\n"
            "if os.path.exists(zip_path):\n"
            "    size_mb = os.path.getsize(zip_path) / 1024 / 1024\n"
            "    print(f'Size: {size_mb:.1f} MB')\n"
            "    try:\n"
            "        from google.colab import files\n"
            "        files.download(zip_path)\n"
            "        print('Download started!')\n"
            "    except ImportError:\n"
            "        print(f'Not in Colab. Results at: {zip_path}')\n"
            "else:\n"
            "    print('No zip created. Check that the sweep produced results.')\n"
            "\n"
            f"print(f'\\nTo apply: cd {local_project_path} && unzip ~/Downloads/mutation_results.zip')"
        ),
    ]

    return {
        "cells": cells,
        "metadata": {
            "colab": {"provenance": [], "name": "LintGate Mutation Sweep"},
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
            "language_info": {"name": "python", "version": "3.11.0"},
        },
        "nbformat": 4,
        "nbformat_minor": 0,
    }


def impl_colab_sweep_generate(
    helpers: Any,
    path: str,
    workers: int = 4,
    budget_ms: int = 500,
    output: str = "",
) -> str:
    """Generate a Colab notebook for offloading mutation sweeps."""
    from lintgate.next_action import NextAction, serialize_next_actions

    project_root = helpers["_validate_project_root"](path)

    git_info = _get_git_info(project_root)
    cached_count = _count_cached_profiles(project_root)
    source_count = _count_source_files(project_root)

    if not git_info["repo_url"]:
        return json.dumps({
            "error": "No git remote found. The Colab notebook needs a cloneable repo URL.",
            "hint": "Run: git remote add origin <your-repo-url>",
        })

    notebook = _build_notebook(
        repo_url=git_info["repo_url"],
        branch=git_info["branch"],
        workers=workers,
        budget_ms=budget_ms,
        cached_count=cached_count,
        source_count=source_count,
        local_project_path=project_root,
    )

    # Determine output path
    if not output:
        output = os.path.join(project_root, "scripts", "LintGate_Mutation_Sweep.ipynb")

    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(notebook, f, indent=1)

    next_actions = [
        NextAction(
            tool="platonic_sweep",
            args={"path": path},
            reason="Run local sweep while Colab handles the batch",
        ),
    ]

    return json.dumps({
        "notebook_path": output,
        "repo_url": git_info["repo_url"],
        "branch": git_info["branch"],
        "source_files": source_count,
        "cached_profiles": cached_count,
        "estimated_new": max(0, source_count - cached_count),
        "workers": workers,
        "budget_ms": budget_ms,
        "instructions": (
            f"Upload {os.path.basename(output)} to https://colab.research.google.com/ "
            "then Runtime > Run all. Download the zip when done and unzip into the project root."
        ),
        "sync_command": f"cd {project_root} && unzip ~/Downloads/mutation_results.zip",
        "next_actions": serialize_next_actions(next_actions),
    })

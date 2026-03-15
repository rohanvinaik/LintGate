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

_LINTGATE_REPO_URL = "https://github.com/rohanvinaik/LintGate.git"


def _get_lintgate_repo_info() -> tuple[str, str]:
    """Get the LintGate repo URL and current branch."""
    url = _LINTGATE_REPO_URL
    branch = "main"
    try:
        lintgate_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        info = _get_git_info(lintgate_dir)
        if info["repo_url"]:
            url = info["repo_url"]
        if info["branch"]:
            branch = info["branch"]
    except Exception:
        pass
    return url, branch


def _get_lintgate_repo_url() -> str:
    """Get the LintGate repo URL (backward compat wrapper)."""
    return _get_lintgate_repo_info()[0]


def _is_self_analysis(repo_url: str) -> bool:
    """Check if the target repo IS lintgate itself."""
    lintgate_url = _get_lintgate_repo_url()
    return repo_url.rstrip("/").rstrip(".git").lower() == lintgate_url.rstrip("/").rstrip(".git").lower()


def _build_install_cell(
    repo_url: str,
    branch: str,
    src_dirs_str: str,
    *,
    self_analysis: bool,
    lintgate_branch: str = "main",
) -> str:
    """Build the install cell for either self-analysis or external project mode.

    Self-analysis: single clone (LintGate = both tool and target).
    External: two clones — LintGate as tool, target project as subject.
    """
    if self_analysis:
        return (
            "import importlib, os, shutil, subprocess, sys\n"
            "\n"
            f'REPO_URL = "{repo_url}"\n'
            f'BRANCH = "{branch}"\n'
            'PROJECT_DIR = "/content/project"\n'
            f"SRC_DIRS = {src_dirs_str}\n"
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
            "    result = subprocess.run(\n"
            "        ['git', 'clone', '--depth', '1', clone_url, PROJECT_DIR],\n"
            "        capture_output=True, text=True\n"
            "    )\n"
            "    if result.returncode != 0:\n"
            "        raise RuntimeError(f'Clone failed: {result.stderr}')\n"
            "print(f'Cloned to {PROJECT_DIR}')\n"
            "\n"
            "subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'hatchling', 'pyyaml', 'packaging'], check=True)\n"
            "subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-e', PROJECT_DIR], capture_output=True, text=True)\n"
            "if PROJECT_DIR not in sys.path:\n"
            "    sys.path.insert(0, PROJECT_DIR)\n"
            "for m in list(sys.modules):\n"
            "    if m.startswith('lintgate') or m.startswith('mcp_tools'):\n"
            "        del sys.modules[m]\n"
            "importlib.invalidate_caches()\n"
            "print('Self-analysis mode: LintGate is both tool and target')"
        )

    lintgate_url = _get_lintgate_repo_url()
    return (
        "import importlib, os, shutil, subprocess, sys\n"
        "\n"
        f'TARGET_REPO_URL = "{repo_url}"\n'
        f'TARGET_BRANCH = "{branch}"\n'
        f'LINTGATE_REPO_URL = "{lintgate_url}"\n'
        'LINTGATE_DIR = "/content/lintgate"\n'
        'PROJECT_DIR = "/content/project"\n'
        f"SRC_DIRS = {src_dirs_str}\n"
        "\n"
        "# Uncomment if repos are private:\n"
        '# GITHUB_TOKEN = "ghp_xxxxxxxxxxxxxxxxxxxx"\n'
        "\n"
        "def _clone(url, branch, dest):\n"
        "    if os.path.exists(dest):\n"
        "        shutil.rmtree(dest)\n"
        "    clone_url = url\n"
        "    try:\n"
        "        GITHUB_TOKEN\n"
        '        clone_url = url.replace("https://", f"https://{GITHUB_TOKEN}@")\n'
        "    except NameError:\n"
        "        pass\n"
        "    result = subprocess.run(\n"
        "        ['git', 'clone', '--depth', '1', '--branch', branch, clone_url, dest],\n"
        "        capture_output=True, text=True\n"
        "    )\n"
        "    if result.returncode != 0:\n"
        "        result = subprocess.run(\n"
        "            ['git', 'clone', '--depth', '1', clone_url, dest],\n"
        "            capture_output=True, text=True\n"
        "        )\n"
        "        if result.returncode != 0:\n"
        "            raise RuntimeError(f'Clone {url} failed: {result.stderr}')\n"
        "    print(f'Cloned {url} → {dest}')\n"
        "\n"
        "# Step 1: Install LintGate (the analysis tool)\n"
        "print('Installing LintGate...')\n"
        f"_clone(LINTGATE_REPO_URL, '{lintgate_branch}', LINTGATE_DIR)\n"
        "subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'hatchling', 'pyyaml', 'packaging'], check=True)\n"
        "subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-e', LINTGATE_DIR], capture_output=True, text=True)\n"
        "if LINTGATE_DIR not in sys.path:\n"
        "    sys.path.insert(0, LINTGATE_DIR)\n"
        "\n"
        "# Step 2: Clone the target project\n"
        "print('Cloning target project...')\n"
        "_clone(TARGET_REPO_URL, TARGET_BRANCH, PROJECT_DIR)\n"
        "\n"
        "# Verify LintGate is importable\n"
        "for m in list(sys.modules):\n"
        "    if m.startswith('lintgate') or m.startswith('mcp_tools'):\n"
        "        del sys.modules[m]\n"
        "importlib.invalidate_caches()\n"
        "from lintgate.specification.mutation_engine import MutationCategory\n"
        "print(f'LintGate installed. Categories: {[c.value for c in MutationCategory]}')\n"
        "print(f'Target project at {PROJECT_DIR}')\n"
        f"print(f'Source dirs: {src_dirs_str}')"
    )


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
    workers: int,
    budget_ms: int,
    cached_count: int,
    source_count: int,
    local_project_path: str,
    src_dirs: list[str] | None = None,
) -> dict[str, Any]:
    """Build the .ipynb notebook structure."""
    if src_dirs is None:
        src_dirs = ["lintgate", "mcp_tools"]
    src_dirs_str = repr(src_dirs)
    self_analysis = _is_self_analysis(repo_url)
    _, lg_branch = _get_lintgate_repo_info()

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
        "import ast, json, os, sys, time\n"
        "from concurrent.futures import ProcessPoolExecutor, as_completed\n"
        "from pathlib import Path\n"
        "\n"
        f"WORKERS = {workers}\n"
        f"BUDGET_MS = {budget_ms}.0\n"
        "SKIP_CACHED = False  # Fresh clone — profile everything\n"
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
        "    result = {'file': rel_path, 'profiled': 0, 'cached': 0, 'trivial': 0,\n"
        "              'killed': 0, 'survived': 0, 'errors': 0, 'discovery': {}}\n"
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
        "        tests, diag = load_test_callables(\n"
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
        "            if diag:\n"
        "                from dataclasses import asdict as _asdict\n"
        "                rd['discovery_diagnostics'] = _asdict(diag) if hasattr(diag, '__dataclass_fields__') else diag\n"
        "            # Classify discovery state\n"
        "            if not ctx.test_files:\n"
        "                ds = 'NO_TEST_FILES'\n"
        "            elif len(tests) == 0:\n"
        "                ds = 'NO_TESTS_LINKED'\n"
        "            elif sr.total_killed == 0 and sr.total_mutants == 0:\n"
        "                ds = 'EQUIVALENT'\n"
        "            elif sr.total_killed == 0 and sr.total_mutants > 0:\n"
        "                ds = 'ZERO_KILLS'\n"
        "            else:\n"
        "                ds = 'OK'\n"
        "            rd['discovery_state'] = ds\n"
        "            save_cached_state(ctx.cache_dir, func_key, rd)\n"
        "            result['profiled'] += 1\n"
        "            result['killed'] += sr.total_killed\n"
        "            result['survived'] += sr.total_survived\n"
        "            result['discovery'][ds] = result['discovery'].get(ds, 0) + 1\n"
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
        "totals = {'profiled': 0, 'cached': 0, 'trivial': 0, 'killed': 0, 'survived': 0, 'errors': 0}\n"
        "disc = {}\n"
        "start = time.monotonic()\n"
        "with ProcessPoolExecutor(max_workers=WORKERS) as pool:\n"
        "    futures = {pool.submit(profile_single_file, w): w[1] for w in work}\n"
        "    for i, future in enumerate(as_completed(futures), 1):\n"
        "        rel = futures[future]\n"
        "        try:\n"
        "            r = future.result()\n"
        "            for k in ('profiled', 'cached', 'trivial', 'killed', 'survived', 'errors'):\n"
        "                totals[k] += r.get(k, 0)\n"
        "            for ds, n in r.get('discovery', {}).items():\n"
        "                disc[ds] = disc.get(ds, 0) + n\n"
        "            print(f'[{i}/{len(files)}] {rel}: {r.get(\"profiled\",0)} profiled, '\n"
        "                  f'{r.get(\"cached\",0)} cached, {r.get(\"elapsed_s\",\"?\")}s')\n"
        "        except Exception as e:\n"
        "            print(f'[{i}/{len(files)}] {rel}: FAILED - {e}')\n"
        "            totals['errors'] += 1\n"
        "elapsed = round(time.monotonic() - start, 1)\n"
        "t = totals['killed'] + totals['survived']\n"
        "kr = f'{totals[\"killed\"]}/{t} ({totals[\"killed\"]/t:.1%})' if t else 'N/A'\n"
        "print(f'\\n{\"=\"*60}')\n"
        "print(f'Done in {elapsed}s')\n"
        "print(f'  Profiled:  {totals[\"profiled\"]}')\n"
        "print(f'  Cached:    {totals[\"cached\"]}')\n"
        "print(f'  Trivial:   {totals[\"trivial\"]}')\n"
        "print(f'  Errors:    {totals[\"errors\"]}')\n"
        "print(f'  Kill rate: {kr}')\n"
        "print(f'  Discovery:')\n"
        "for ds, n in sorted(disc.items()):\n"
        "    print(f'    {ds}: {n}')\n"
        "# Write summary JSON\n"
        "import json as _json\n"
        "summary = {'profiled': totals['profiled'], 'killed': totals['killed'],\n"
        "           'survived': totals['survived'], 'kill_rate': round(totals['killed']/t, 4) if t else 0,\n"
        "           'discovery_states': disc, 'errors': totals['errors'], 'elapsed_s': elapsed}\n"
        "with open(cache_dir / 'sweep_summary.json', 'w') as sf:\n"
        "    _json.dump(summary, sf, indent=2)\n"
        "print(f'\\nSummary written to .lintgate/mutation/sweep_summary.json')"
    )

    cells = [
        _md(
            "# LintGate Mutation Sweep\n"
            "\n"
            "**Auto-generated** for `main` branch. Fully self-contained.\n"
            "\n"
            f"- Source files to profile: **{source_count}**\n"
            f"- Already cached locally: **{cached_count}**\n"
            f"- Estimated new profiles: **~{max(0, source_count - cached_count)}**\n"
            "\n"
            "**How to use:** Runtime > Run all. Wait. Download the zip at the end.\n"
            f"Then: `cd {local_project_path} && unzip ~/Downloads/mutation_results.zip`"
        ),
        _md("## Step 1: Install LintGate + Clone Target"),
        _code(_build_install_cell(repo_url, "main", src_dirs_str, self_analysis=self_analysis, lintgate_branch=lg_branch)),
        _md("## Step 2: Run sweep"),
        _code(sweep_code),
        _md("## Step 3: Review & download"),
        _code(
            "import json, os\n"
            "from pathlib import Path\n"
            "from collections import Counter\n"
            "\n"
            "cache_dir = Path(PROJECT_DIR) / '.lintgate' / 'mutation'\n"
            "files = sorted(cache_dir.glob('*.json')) if cache_dir.exists() else []\n"
            "files = [f for f in files if f.name != 'sweep_summary.json' and f.name != 'scheduler_state.json']\n"
            "print(f'Total profiles: {len(files)}')\n"
            "\n"
            "total_k, total_s = 0, 0\n"
            "high = []\n"
            "disc = Counter()\n"
            "per_file_kills = Counter()\n"
            "per_file_total = Counter()\n"
            "for f in files:\n"
            "    try:\n"
            "        d = json.loads(f.read_text())\n"
            "    except Exception:\n"
            "        continue\n"
            "    k, s = d.get('total_killed', 0), d.get('total_survived', 0)\n"
            "    total_k += k\n"
            "    total_s += s\n"
            "    ds = d.get('discovery_state', 'UNKNOWN')\n"
            "    disc[ds] += 1\n"
            "    fk = d.get('function_key', '?')\n"
            "    src = fk.split('::')[0] if '::' in fk else '?'\n"
            "    per_file_kills[src] += k\n"
            "    per_file_total[src] += k + s\n"
            "    r = d.get('survival_rate', 0)\n"
            "    if r > 0.5 and (k + s) > 0:\n"
            "        high.append((fk, r))\n"
            "\n"
            "t = total_k + total_s\n"
            "print(f'Kill rate: {total_k}/{t} ({total_k/t:.1%})' if t else 'No mutants')\n"
            "print(f'\\nDiscovery states:')\n"
            "for ds, n in disc.most_common():\n"
            "    print(f'  {ds}: {n}')\n"
            "print(f'\\nHigh-survival functions ({len(high)}):')\n"
            "for key, r in sorted(high, key=lambda x: -x[1])[:20]:\n"
            "    print(f'  {r:.0%} {key}')\n"
            "print(f'\\nPer-file kill rates (worst 15):')\n"
            "file_rates = [(f, per_file_kills[f], per_file_total[f]) for f in per_file_total if per_file_total[f] > 0]\n"
            "file_rates.sort(key=lambda x: x[1]/x[2] if x[2] else 1)\n"
            "for f, k, t in file_rates[:15]:\n"
            "    print(f'  {k}/{t} ({k/t:.0%}) {f}')"
        ),
        _md("## Step 4: Flat test coverage analysis"),
        _code(
            "import os, re\n"
            "from pathlib import Path\n"
            "\n"
            "SRC_DIRS = ['lintgate', 'mcp_tools']\n"
            "TEST_DIR = os.path.join(PROJECT_DIR, 'tests')\n"
            "MIN_LOC = 0\n"
            "\n"
            "def count_lines(path):\n"
            "    try:\n"
            "        return sum(1 for _ in open(path, encoding='utf-8', errors='ignore'))\n"
            "    except OSError:\n"
            "        return 0\n"
            "\n"
            "def find_test_file(base_name, test_dir):\n"
            "    \"\"\"Find a matching test file for a production module.\"\"\"\n"
            "    # Strip leading underscore for matching\n"
            "    clean = base_name.lstrip('_')\n"
            "    candidates = [\n"
            "        f'test_{base_name}.py',\n"
            "        f'test_{clean}.py',\n"
            "    ]\n"
            "    # Also check for partial matches\n"
            "    for c in candidates:\n"
            "        p = os.path.join(test_dir, c)\n"
            "        if os.path.isfile(p):\n"
            "            return p\n"
            "    # Fuzzy: any test file containing the base name\n"
            "    if os.path.isdir(test_dir):\n"
            "        for f in os.listdir(test_dir):\n"
            "            if f.startswith('test_') and clean in f and f.endswith('.py'):\n"
            "                return os.path.join(test_dir, f)\n"
            "    return None\n"
            "\n"
            "# Collect all large production files\n"
            "large_files = []\n"
            "for subdir in SRC_DIRS:\n"
            "    base = os.path.join(PROJECT_DIR, subdir)\n"
            "    if not os.path.isdir(base):\n"
            "        continue\n"
            "    for dirpath, _, filenames in os.walk(base):\n"
            "        for fn in sorted(filenames):\n"
            "            if not fn.endswith('.py') or fn == '__init__.py':\n"
            "                continue\n"
            "            if fn.startswith('test_') or fn.endswith('_test.py'):\n"
            "                continue\n"
            "            full = os.path.join(dirpath, fn)\n"
            "            loc = count_lines(full)\n"
            "            if loc >= MIN_LOC:\n"
            "                rel = os.path.relpath(full, PROJECT_DIR)\n"
            "                large_files.append((rel, loc, fn))\n"
            "\n"
            "# Match to test files and compute ratios\n"
            "rows = []\n"
            "for rel, src_loc, fn in large_files:\n"
            "    base_name = fn[:-3]  # strip .py\n"
            "    test_path = find_test_file(base_name, TEST_DIR)\n"
            "    if test_path:\n"
            "        test_loc = count_lines(test_path)\n"
            "        test_name = os.path.basename(test_path)\n"
            "    else:\n"
            "        test_loc = 0\n"
            "        test_name = 'NONE'\n"
            "    ratio = test_loc / src_loc if src_loc > 0 else 0\n"
            "    rows.append((rel, src_loc, test_loc, ratio, test_name))\n"
            "\n"
            "# Sort by ratio ascending (worst coverage first)\n"
            "rows.sort(key=lambda r: r[3])\n"
            "\n"
            "# Print report\n"
            "print(f'Test Coverage Analysis: {len(rows)} production files >= {MIN_LOC} LoC')\n"
            "print(f'{\"=\"*90}')\n"
            "print(f'{\"Source File\":<55} {\"Src\":>5} {\"Test\":>5} {\"Ratio\":>6} {\"Test File\"}')\n"
            "print(f'{\"-\"*90}')\n"
            "\n"
            "no_test = [r for r in rows if r[4] == 'NONE']\n"
            "low_cov = [r for r in rows if r[4] != 'NONE' and r[3] < 0.5]\n"
            "good_cov = [r for r in rows if r[4] != 'NONE' and r[3] >= 0.5]\n"
            "\n"
            "print(f'\\n## NO TEST FILE ({len(no_test)} files, {sum(r[1] for r in no_test)} LoC)')\n"
            "for rel, src, tst, ratio, tname in no_test:\n"
            "    print(f'  {rel:<55} {src:>5}   ---    ---  {tname}')\n"
            "\n"
            "print(f'\\n## LOW COVERAGE <0.5x ({len(low_cov)} files)')\n"
            "for rel, src, tst, ratio, tname in low_cov:\n"
            "    print(f'  {rel:<55} {src:>5} {tst:>5} {ratio:>5.2f}x {tname}')\n"
            "\n"
            "print(f'\\n## ADEQUATE COVERAGE >=0.5x ({len(good_cov)} files)')\n"
            "for rel, src, tst, ratio, tname in good_cov:\n"
            "    print(f'  {rel:<55} {src:>5} {tst:>5} {ratio:>5.2f}x {tname}')\n"
            "\n"
            "total_src = sum(r[1] for r in rows)\n"
            "total_tst = sum(r[2] for r in rows)\n"
            "print(f'\\n{\"=\"*90}')\n"
            "print(f'Total: {total_src} src LoC, {total_tst} test LoC, {total_tst/total_src:.2f}x overall ratio')\n"
            "print(f'No test file: {len(no_test)}/{len(rows)} ({len(no_test)/len(rows):.0%})')\n"
            "print(f'Low coverage: {len(low_cov)}/{len(rows)} ({len(low_cov)/len(rows):.0%})')\n"
            "print(f'Adequate:     {len(good_cov)}/{len(rows)} ({len(good_cov)/len(rows):.0%})')\n"
            "\n"
            "# Write coverage summary JSON\n"
            "import json\n"
            "cov_summary = {\n"
            "    'total_large_files': len(rows),\n"
            "    'no_test_file': [{'file': r[0], 'loc': r[1]} for r in no_test],\n"
            "    'low_coverage': [{'file': r[0], 'loc': r[1], 'test_loc': r[2], 'ratio': round(r[3], 2)} for r in low_cov],\n"
            "    'adequate': [{'file': r[0], 'loc': r[1], 'test_loc': r[2], 'ratio': round(r[3], 2)} for r in good_cov],\n"
            "    'totals': {'src_loc': total_src, 'test_loc': total_tst, 'ratio': round(total_tst/total_src, 3)},\n"
            "}\n"
            "cov_path = Path(PROJECT_DIR) / '.lintgate' / 'mutation' / 'coverage_analysis.json'\n"
            "with open(cov_path, 'w') as cf:\n"
            "    json.dump(cov_summary, cf, indent=2)\n"
            "print(f'\\nCoverage analysis written to .lintgate/mutation/coverage_analysis.json')"
        ),
        _md("## Step 5: Download results"),
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


def _build_golden_path_notebook(
    repo_url: str,
    workers: int,
    budget_ms: int,
    source_count: int,
    local_project_path: str,
    src_dirs: list[str] | None = None,
) -> dict[str, Any]:
    """Build the golden path .ipynb: sweep + spec analysis + coverage."""
    if src_dirs is None:
        src_dirs = ["lintgate", "mcp_tools"]
    src_dirs_str = repr(src_dirs)
    self_analysis = _is_self_analysis(repo_url)
    _, lg_branch = _get_lintgate_repo_info()

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

    install_cell = _build_install_cell(repo_url, "main", src_dirs_str, self_analysis=self_analysis, lintgate_branch=lg_branch)

    # Step 2: Mutation sweep (same proven logic)
    sweep_cell = (
        "import ast, json, os, sys, time\n"
        "from concurrent.futures import ProcessPoolExecutor, as_completed\n"
        "from pathlib import Path\n"
        "\n"
        f"WORKERS = {workers}\n"
        f"BUDGET_MS = {budget_ms}.0\n"
        "\n"
        "def collect_python_files(root):\n"
        "    files = []\n"
        "    for subdir in SRC_DIRS:\n"
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
        "    project_root, rel_path, budget_ms = args\n"
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
        "    result = {'file': rel_path, 'profiled': 0, 'trivial': 0, 'killed': 0, 'survived': 0, 'errors': 0}\n"
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
        "        body = getattr(node, 'body', [])\n"
        "        if len(body) <= 1:\n"
        "            stmt = body[0] if body else None\n"
        "            if isinstance(stmt, (ast.Return, ast.Expr)):\n"
        "                result['trivial'] += 1\n"
        "                continue\n"
        "        is_pure = lookup_purity(purity_map, qualname)\n"
        "        cats = filter_categories(node, is_pure=is_pure)\n"
        "        bare_name = qualname.split('.')[-1]\n"
        "        tests, diag = load_test_callables(\n"
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
        "            if diag:\n"
        "                from dataclasses import asdict as _asdict\n"
        "                rd['discovery_diagnostics'] = _asdict(diag) if hasattr(diag, '__dataclass_fields__') else diag\n"
        "            if not ctx.test_files:\n"
        "                ds = 'NO_TEST_FILES'\n"
        "            elif len(tests) == 0:\n"
        "                ds = 'NO_TESTS_LINKED'\n"
        "            elif sr.total_killed == 0 and sr.total_mutants == 0:\n"
        "                ds = 'EQUIVALENT'\n"
        "            elif sr.total_killed == 0 and sr.total_mutants > 0:\n"
        "                ds = 'ZERO_KILLS'\n"
        "            else:\n"
        "                ds = 'OK'\n"
        "            rd['discovery_state'] = ds\n"
        "            save_cached_state(ctx.cache_dir, func_key, rd)\n"
        "            result['profiled'] += 1\n"
        "            result['killed'] += sr.total_killed\n"
        "            result['survived'] += sr.total_survived\n"
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
        "work = [(project_root, f, BUDGET_MS) for f in files]\n"
        "totals = {'profiled': 0, 'trivial': 0, 'killed': 0, 'survived': 0, 'errors': 0}\n"
        "start = time.monotonic()\n"
        "with ProcessPoolExecutor(max_workers=WORKERS) as pool:\n"
        "    futures = {pool.submit(profile_single_file, w): w[1] for w in work}\n"
        "    for i, future in enumerate(as_completed(futures), 1):\n"
        "        rel = futures[future]\n"
        "        try:\n"
        "            r = future.result()\n"
        "            for k in totals:\n"
        "                totals[k] += r.get(k, 0)\n"
        "            if i % 50 == 0 or i == len(files):\n"
        "                print(f'[{i}/{len(files)}] {totals[\"profiled\"]} profiled, {totals[\"killed\"]}k/{totals[\"survived\"]}s')\n"
        "        except Exception as e:\n"
        "            print(f'[{i}/{len(files)}] {rel}: FAILED - {e}')\n"
        "            totals['errors'] += 1\n"
        "elapsed = round(time.monotonic() - start, 1)\n"
        "t = totals['killed'] + totals['survived']\n"
        "kr = f'{totals[\"killed\"]}/{t} ({totals[\"killed\"]/t:.1%})' if t else 'N/A'\n"
        "print(f'\\n{\"=\"*60}')\n"
        "print(f'Sweep done in {elapsed}s | Kill rate: {kr}')\n"
        "print(f'Profiled: {totals[\"profiled\"]} | Trivial: {totals[\"trivial\"]} | Errors: {totals[\"errors\"]}')"
    )

    # Step 3: Spec analysis + reconciliation
    spec_cell = (
        "import json, os\n"
        "from pathlib import Path\n"
        "from collections import Counter\n"
        "\n"
        "project_root = os.path.abspath(PROJECT_DIR)\n"
        "cache_dir = Path(project_root) / '.lintgate' / 'mutation'\n"
        "profiles = {}\n"
        "for f in cache_dir.glob('*.json'):\n"
        "    if f.name in ('sweep_summary.json', 'scheduler_state.json'):\n"
        "        continue\n"
        "    try:\n"
        "        d = json.loads(f.read_text())\n"
        "        profiles[d.get('function_key', '')] = d\n"
        "    except Exception:\n"
        "        pass\n"
        "\n"
        "from lintgate.specification.file_analyzer import analyze_file\n"
        "from lintgate.specification.static_empirical_reconciliation import build_overlay, reconcile_spec_level\n"
        "from lintgate.specification.gap_classifier import classify_from_func_data\n"
        "\n"
        "files = collect_python_files(project_root)\n"
        "gap_dist, regime_dist, phase_dist = Counter(), Counter(), Counter()\n"
        "under_specified = []\n"
        "total_funcs, total_spec, total_reconciled = 0, 0.0, 0.0\n"
        "\n"
        "for rel_file in files:\n"
        "    try:\n"
        "        result = analyze_file(project_root, rel_file)\n"
        "    except Exception:\n"
        "        continue\n"
        "    if not result or not result.functions:\n"
        "        continue\n"
        "    for func_key, func_data in result.functions.items():\n"
        "        if not isinstance(func_data, dict):\n"
        "            continue\n"
        "        total_funcs += 1\n"
        "        sigma = func_data.get('sigma', 0) or 0\n"
        "        regime = func_data.get('regime', 'A')\n"
        "        phase = func_data.get('phase', 'bulk')\n"
        "        static_spec = func_data.get('specification_level', 0.0)\n"
        "        regime_dist[regime] += 1\n"
        "        phase_dist[phase] += 1\n"
        "        total_spec += static_spec\n"
        "        overlay = build_overlay(func_key, int(sigma), regime, phase, profiles or None)\n"
        "        reconciled, source = reconcile_spec_level(static_spec, overlay)\n"
        "        total_reconciled += reconciled\n"
        "        mutation_entry = profiles.get(func_key)\n"
        "        gap = classify_from_func_data(func_data, mutation_entry).value\n"
        "        gap_dist[gap] += 1\n"
        "        kill_rate = 1.0 - mutation_entry.get('survival_rate', 1.0) if mutation_entry else None\n"
        "        if reconciled < 0.5 and sigma > 3:\n"
        "            under_specified.append((rel_file, func_key, static_spec, reconciled, kill_rate, sigma))\n"
        "\n"
        "ms = total_spec / total_funcs if total_funcs else 0\n"
        "mr = total_reconciled / total_funcs if total_funcs else 0\n"
        "print(f'Functions: {total_funcs} | spec_level: {ms:.3f} static / {mr:.3f} reconciled')\n"
        "print(f'Regime: {dict(regime_dist.most_common())}')  \n"
        "print(f'Phase: {dict(phase_dist.most_common())}')\n"
        "print(f'\\nGap classification:')\n"
        "for gap, n in gap_dist.most_common():\n"
        "    print(f'  {gap}: {n}')\n"
        "under_specified.sort(key=lambda x: -(x[5] or 0))\n"
        "print(f'\\nUnder-specified (reconciled < 0.5, sigma > 3): {len(under_specified)}')\n"
        "for f, fk, ss, rs, kr, sig in under_specified[:20]:\n"
        "    kr_s = f'{kr:.0%}' if kr is not None else '?'\n"
        "    print(f'  sigma={sig:>3} spec={rs:.2f} kill={kr_s} {fk}')"
    )

    # Step 4: Per-file kill rates
    kill_rate_cell = (
        "import json\n"
        "from pathlib import Path\n"
        "from collections import Counter\n"
        "\n"
        "cache_dir = Path(PROJECT_DIR) / '.lintgate' / 'mutation'\n"
        "pfiles = [f for f in cache_dir.glob('*.json') if f.name not in ('sweep_summary.json', 'scheduler_state.json')]\n"
        "total_k, total_s = 0, 0\n"
        "per_file = {}\n"
        "for f in pfiles:\n"
        "    try:\n"
        "        d = json.loads(f.read_text())\n"
        "    except Exception:\n"
        "        continue\n"
        "    k, s = d.get('total_killed', 0), d.get('total_survived', 0)\n"
        "    total_k += k\n"
        "    total_s += s\n"
        "    fk = d.get('function_key', '?')\n"
        "    src = fk.split('::')[0] if '::' in fk else '?'\n"
        "    if src not in per_file:\n"
        "        per_file[src] = {'killed': 0, 'survived': 0, 'funcs': 0}\n"
        "    per_file[src]['killed'] += k\n"
        "    per_file[src]['survived'] += s\n"
        "    per_file[src]['funcs'] += 1\n"
        "t = total_k + total_s\n"
        "print(f'Aggregate: {total_k}/{t} ({total_k/t:.1%})' if t else 'No data')\n"
        "rated = []\n"
        "for src, d in per_file.items():\n"
        "    tt = d['killed'] + d['survived']\n"
        "    kr = d['killed'] / tt if tt > 0 else -1\n"
        "    rated.append((src, d['killed'], d['survived'], tt, kr, d['funcs']))\n"
        "rated.sort(key=lambda x: x[4])\n"
        "fk_count = sum(1 for r in rated if r[4] == 1.0)\n"
        "zk_count = sum(1 for r in rated if r[4] == 0.0 and r[3] > 0)\n"
        "print(f'{fk_count} full-kill | {len(rated)-fk_count-zk_count} partial | {zk_count} zero-kill')\n"
        "print(f'\\n--- Zero-kill ---')\n"
        "for src, k, s, tt, kr, funcs in rated:\n"
        "    if kr == 0.0 and tt > 0:\n"
        "        print(f'  {tt:>4} mutants  {funcs:>3} funcs  {src}')\n"
        "print(f'\\n--- Partial (worst 15) ---')\n"
        "partial = [(src, k, s, tt, kr) for src, k, s, tt, kr, _ in rated if 0 < kr < 1.0]\n"
        "for src, k, s, tt, kr in partial[:15]:\n"
        "    print(f'  {kr:>5.0%} ({k}/{tt})  {src}')\n"
        "summary = {'profiles': len(pfiles), 'killed': total_k, 'survived': total_s,\n"
        "           'kill_rate': round(total_k/t, 4) if t else 0, 'full_kill': fk_count, 'zero_kill': zk_count}\n"
        "with open(cache_dir / 'sweep_summary.json', 'w') as sf:\n"
        "    json.dump(summary, sf, indent=2)\n"
        "print(f'\\nSummary written.')"
    )

    # Step 5: Download
    download_cell = (
        "import os, subprocess\n"
        "from pathlib import Path\n"
        "\n"
        "mutation_dir = Path(PROJECT_DIR) / '.lintgate' / 'mutation'\n"
        "mutation_dir.mkdir(parents=True, exist_ok=True)\n"
        "\n"
        "zip_path = '/content/golden_path_results.zip'\n"
        "subprocess.run(['zip', '-r', zip_path, '.lintgate/mutation/'],\n"
        "               cwd=PROJECT_DIR, capture_output=True)\n"
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
        "    print('No zip created.')\n"
        "\n"
        f"print(f'\\nTo apply: cd {local_project_path} && unzip ~/Downloads/golden_path_results.zip')"
    )

    cells = [
        _md(
            "# LintGate Golden Path Pipeline\n"
            "\n"
            "Full specification coverage pipeline: **Sweep → Analyze → Reconcile → Report**\n"
            "\n"
            f"- Source files: **{source_count}**\n"
            f"- Workers: **{workers}** | Budget: **{budget_ms}ms**\n"
            "\n"
            "**Runtime > Run all.** ~5-10 min."
        ),
        _md("## Step 1: Clone & install"),
        _code(install_cell),
        _md("## Step 2: Mutation sweep"),
        _code(sweep_cell),
        _md("## Step 3: Specification analysis + reconciliation"),
        _code(spec_cell),
        _md("## Step 4: Per-file kill rates"),
        _code(kill_rate_cell),
        _md("## Step 5: Download results"),
        _code(download_cell),
    ]

    return {
        "cells": cells,
        "metadata": {
            "colab": {"provenance": [], "name": "LintGate Golden Path Pipeline"},
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
    mode: str = "sweep",
) -> str:
    """Generate a Colab notebook for offloading mutation sweeps.

    Args:
        mode: "sweep" for mutation-only, "golden_path" for full pipeline.
    """
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

    # Detect source directories for the target project
    skip = {"tests", "test", "docs", "scripts", "node_modules", "venv", ".venv", "__pycache__"}
    src_dirs: list[str] = []
    for entry in sorted(os.listdir(project_root)):
        if entry.startswith(".") or entry.startswith("_") or entry in skip:
            continue
        full = os.path.join(project_root, entry)
        if not os.path.isdir(full):
            continue
        has_py = any(f.endswith(".py") for f in os.listdir(full) if os.path.isfile(os.path.join(full, f)))
        has_init = os.path.isfile(os.path.join(full, "__init__.py"))
        if has_py or has_init:
            src_dirs.append(entry)
    if not src_dirs:
        src_dirs = ["."]

    if mode == "golden_path":
        notebook = _build_golden_path_notebook(
            repo_url=git_info["repo_url"],
            workers=workers,
            budget_ms=budget_ms,
            source_count=source_count,
            local_project_path=project_root,
            src_dirs=src_dirs,
        )
        default_name = "Golden_Path_Pipeline.ipynb"
    else:
        notebook = _build_notebook(
            repo_url=git_info["repo_url"],
            workers=workers,
            budget_ms=budget_ms,
            cached_count=cached_count,
            source_count=source_count,
            local_project_path=project_root,
            src_dirs=src_dirs,
        )
        default_name = "LintGate_Mutation_Sweep.ipynb"

    # Determine output path
    if not output:
        output = os.path.join(project_root, "scripts", default_name)

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
        "mode": mode,
        "repo_url": git_info["repo_url"],
        "branch": git_info["branch"],
        "source_files": source_count,
        "cached_profiles": cached_count,
        "estimated_new": max(0, source_count - cached_count),
        "workers": workers,
        "budget_ms": budget_ms,
        "src_dirs": src_dirs,
        "instructions": (
            f"Upload {os.path.basename(output)} to https://colab.research.google.com/ "
            "then Runtime > Run all. Download the zip when done and unzip into the project root."
        ),
        "sync_command": f"cd {project_root} && unzip ~/Downloads/golden_path_results.zip",
        "next_actions": serialize_next_actions(next_actions),
    })

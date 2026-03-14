"""Implementation for offline_analysis MCP tool.

Generates a self-contained Jupyter notebook that runs the full LintGate
analysis pipeline on any Python project. The notebook is designed for
Google Colab but works in any Jupyter environment.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from lintgate.next_action import NextAction, serialize_next_actions


def _get_git_info(project_root: str) -> dict[str, str]:
    """Extract repo URL and branch from git."""
    info: dict[str, str] = {"repo_url": "", "branch": "main"}
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=project_root, timeout=5,
        )
        if result.returncode == 0:
            info["repo_url"] = result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=project_root, timeout=5,
        )
        if result.returncode == 0:
            info["branch"] = result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return info


def _detect_src_dirs(project_root: str) -> list[str]:
    """Auto-detect source directories."""
    dirs: list[str] = []
    skip = {"tests", "test", "docs", "scripts", "node_modules", "venv", ".venv", "__pycache__"}
    for entry in sorted(os.listdir(project_root)):
        if entry.startswith(".") or entry.startswith("_") or entry in skip:
            continue
        full = os.path.join(project_root, entry)
        if not os.path.isdir(full):
            continue
        has_py = any(f.endswith(".py") for f in os.listdir(full) if os.path.isfile(os.path.join(full, f)))
        has_init = os.path.isfile(os.path.join(full, "__init__.py"))
        if has_py or has_init:
            dirs.append(entry)
    return dirs or ["."]


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


def impl_offline_analysis_generate(
    helpers: dict[str, Any],
    path: str,
    workers: int,
    mutation_budget_ms: int,
    output: str,
    include_mutation: bool,
) -> str:
    """Generate a comprehensive offline analysis notebook."""
    project_root = helpers["_validate_project_root"](path)
    git_info = _get_git_info(project_root)
    repo_url = git_info["repo_url"]
    branch = git_info["branch"]
    src_dirs = _detect_src_dirs(project_root)

    if not repo_url:
        return json.dumps({
            "error": "No git remote found. The notebook needs a repo URL to clone.",
            "hint": "Run: git remote add origin <your-repo-url>",
        })

    notebook = _build_full_analysis_notebook(
        repo_url=repo_url,
        branch=branch,
        workers=workers,
        mutation_budget_ms=mutation_budget_ms,
        include_mutation=include_mutation,
        src_dirs=src_dirs,
        local_project_path=project_root,
    )

    # Write notebook
    if not output:
        scripts_dir = os.path.join(project_root, "scripts")
        os.makedirs(scripts_dir, exist_ok=True)
        output = os.path.join(scripts_dir, "lintgate_full_analysis.ipynb")

    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(notebook, f, indent=1)

    result = {
        "notebook_path": output,
        "repo_url": repo_url,
        "branch": branch,
        "src_dirs": src_dirs,
        "include_mutation": include_mutation,
        "instructions": (
            "Upload the notebook to Google Colab (or run locally with Jupyter). "
            "Runtime > Run all. The final cell downloads a comprehensive "
            "analysis.json that an LLM agent can consume for systematic fixes."
        ),
        "next_actions": serialize_next_actions([
            NextAction(
                tool="prescriptive_spec_status",
                args={"path": path},
                reason="Check existing prescriptive spec coverage",
            ),
        ]),
    }
    return json.dumps(result, indent=2)


def impl_offline_analysis_run(
    helpers: dict[str, Any],
    path: str,
    include_mutation: bool,
    output: str,
) -> str:
    """Run the full offline analysis locally and save the result."""
    project_root = helpers["_validate_project_root"](path)

    from lintgate.offline_analysis import run_full_analysis

    result = run_full_analysis(
        project_root,
        include_mutation=include_mutation,
    )

    # Save to file
    if not output:
        out_dir = os.path.join(project_root, ".lintgate")
        os.makedirs(out_dir, exist_ok=True)
        output = os.path.join(out_dir, "full_analysis.json")

    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    # Build summary for MCP response
    plan = result.get("action_plan", [])
    p0 = sum(1 for a in plan if a.get("priority", "").startswith("P0"))
    p1 = sum(1 for a in plan if a.get("priority", "").startswith("P1"))
    p2 = sum(1 for a in plan if a.get("priority", "").startswith("P2"))
    p3 = sum(1 for a in plan if a.get("priority", "").startswith("P3"))

    summary = {
        "output_path": output,
        "elapsed_s": result.get("elapsed_s", 0),
        "project": {
            "source_files": result.get("project", {}).get("total_source_files", 0),
            "test_files": result.get("project", {}).get("total_test_files", 0),
            "total_loc": result.get("project", {}).get("total_loc", 0),
        },
        "action_plan_summary": {
            "total_actions": len(plan),
            "P0_blocking": p0,
            "P1_critical": p1,
            "P2_important": p2,
            "P3_improve": p3,
        },
        "lint_summary": {
            "total": result.get("lint", {}).get("total_findings", 0),
            "auto_fixable": result.get("lint", {}).get("auto_fixable", 0),
        },
        "spec_summary": {
            "total_functions": result.get("specification", {}).get("total_functions", 0),
            "under_specified": result.get("specification", {}).get("under_specified_count", 0),
        },
        "top_3_actions": plan[:3],
        "instructions": (
            f"Full analysis saved to {output}. "
            "Pass this file to an LLM coding agent with: "
            "'Implement the action plan in this analysis, starting from rank 1.'"
        ),
        "next_actions": serialize_next_actions([
            NextAction(
                tool="lint_fix",
                args={"path": path},
                reason="Start with auto-fixable lint issues (action plan phase 1)",
            ),
        ]),
    }
    return json.dumps(summary, indent=2)


# ── Notebook builder ──────────────────────────────────────────────────


def _get_lintgate_repo_url() -> str:
    """Get the LintGate repo URL for notebook installation."""
    # Try to get from the lintgate package itself
    try:
        lintgate_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        info = _get_git_info(lintgate_dir)
        if info["repo_url"]:
            return info["repo_url"]
    except Exception:
        pass
    return "https://github.com/rohanvinaik/LintGate.git"


def _build_full_analysis_notebook(
    *,
    repo_url: str,
    branch: str,
    workers: int,
    mutation_budget_ms: int,
    include_mutation: bool,
    src_dirs: list[str],
    local_project_path: str,
) -> dict[str, Any]:
    """Build a self-contained .ipynb for comprehensive offline analysis.

    Architecture: TWO clones on Colab —
    1. LintGate (the tool) → installed as a pip package
    2. Target project → cloned as PROJECT_DIR, analyzed by LintGate
    """
    lintgate_url = _get_lintgate_repo_url()
    src_dirs_str = repr(src_dirs)
    mutation_flag = "True" if include_mutation else "False"

    # Detect if the target IS lintgate (self-analysis mode)
    is_self_analysis = repo_url.rstrip("/").rstrip(".git") == lintgate_url.rstrip("/").rstrip(".git")

    # ── Cell 1: Install LintGate + clone target ───────────────────
    if is_self_analysis:
        # Self-analysis: single clone, LintGate is both tool and target
        install_cell = (
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
            "    if any(m.startswith(d.split('/')[0]) for d in SRC_DIRS):\n"
            "        del sys.modules[m]\n"
            "importlib.invalidate_caches()\n"
            "print('Self-analysis mode: LintGate is both tool and target')"
        )
    else:
        # External project: install LintGate as tool, clone target separately
        install_cell = (
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
            "# Step 1: Clone and install LintGate (the analysis tool)\n"
            "print('Installing LintGate...')\n"
            "_clone(LINTGATE_REPO_URL, 'main', LINTGATE_DIR)\n"
            "subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'hatchling', 'pyyaml', 'packaging'], check=True)\n"
            "subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-e', LINTGATE_DIR], capture_output=True, text=True)\n"
            "if LINTGATE_DIR not in sys.path:\n"
            "    sys.path.insert(0, LINTGATE_DIR)\n"
            "\n"
            "# Step 2: Clone the target project\n"
            "print(f'Cloning target project...')\n"
            f"_clone(TARGET_REPO_URL, TARGET_BRANCH, PROJECT_DIR)\n"
            "\n"
            "# Verify LintGate is importable\n"
            "for m in list(sys.modules):\n"
            "    if m.startswith('lintgate'):\n"
            "        del sys.modules[m]\n"
            "importlib.invalidate_caches()\n"
            "from lintgate.offline_analysis import run_full_analysis\n"
            "print(f'LintGate installed. Target project at {PROJECT_DIR}')\n"
            "print(f'Source dirs to analyze: {SRC_DIRS}')"
        )

    # ── Cell 2: Run full analysis ─────────────────────────────────
    analysis_cell = (
        "import json, os, time\n"
        "\n"
        f"INCLUDE_MUTATION = {mutation_flag}\n"
        f"MUTATION_BUDGET_MS = {mutation_budget_ms}\n"
        "\n"
        "from lintgate.offline_analysis import run_full_analysis\n"
        "\n"
        "print('Starting comprehensive analysis...')\n"
        "print('This runs: lint + specification + composition + performance + test coverage')\n"
        "if INCLUDE_MUTATION:\n"
        "    print('+ mutation profiling (this is the slow part)')\n"
        "print()\n"
        "\n"
        "result = run_full_analysis(\n"
        "    PROJECT_DIR,\n"
        f"    src_dirs={src_dirs_str},\n"
        "    include_mutation=INCLUDE_MUTATION,\n"
        "    mutation_budget_ms=MUTATION_BUDGET_MS,\n"
        ")\n"
        "\n"
        "print(f'Analysis complete in {result[\"elapsed_s\"]}s')\n"
        "print(f'Source files: {result[\"project\"][\"total_source_files\"]}')\n"
        "print(f'Test files: {result[\"project\"][\"total_test_files\"]}')\n"
        "print(f'Total LoC: {result[\"project\"][\"total_loc\"]}')\n"
        "print(f'Lint findings: {result[\"lint\"][\"total_findings\"]} ({result[\"lint\"][\"auto_fixable\"]} auto-fixable)')\n"
        "print(f'Functions analyzed: {result[\"specification\"][\"total_functions\"]}')\n"
        "print(f'Under-specified: {result[\"specification\"][\"under_specified_count\"]}')\n"
        "print(f'Action items: {len(result[\"action_plan\"])}')"
    )

    # ── Cell 3: Display action plan ───────────────────────────────
    plan_cell = (
        "print('=' * 80)\n"
        "print('ACTION PLAN — prioritized fixes for LLM implementation')\n"
        "print('=' * 80)\n"
        "\n"
        "for action in result['action_plan']:\n"
        "    rank = action['rank']\n"
        "    priority = action['priority']\n"
        "    cat = action['category']\n"
        "    f = action.get('file', '')\n"
        "    deps = action.get('depends_on', [])\n"
        "    effort = action.get('estimated_effort', '')\n"
        "    print(f'\\n[{rank}] {priority} | {cat} | {effort}')\n"
        "    print(f'    File: {f}')\n"
        "    if action.get('function'):\n"
        "        print(f'    Function: {action[\"function\"]}')\n"
        "    print(f'    Action: {action[\"action\"]}')\n"
        "    if deps:\n"
        "        print(f'    Depends on: {deps}')\n"
        "\n"
        "print(f'\\n{\"=\" * 80}')\n"
        "p_counts = {}\n"
        "for a in result['action_plan']:\n"
        "    p = a['priority']\n"
        "    p_counts[p] = p_counts.get(p, 0) + 1\n"
        "for p, n in sorted(p_counts.items()):\n"
        "    print(f'  {p}: {n}')"
    )

    # ── Cell 4: Save & download ───────────────────────────────────
    save_cell = (
        "import json, os\n"
        "\n"
        "# Save the full analysis artifact\n"
        "output_path = os.path.join(PROJECT_DIR, '.lintgate', 'full_analysis.json')\n"
        "os.makedirs(os.path.dirname(output_path), exist_ok=True)\n"
        "with open(output_path, 'w') as f:\n"
        "    json.dump(result, f, indent=2)\n"
        "size_kb = os.path.getsize(output_path) / 1024\n"
        "print(f'Analysis saved: {output_path} ({size_kb:.0f} KB)')\n"
        "\n"
        "# Also save a compact action-plan-only file\n"
        "plan_path = os.path.join(PROJECT_DIR, '.lintgate', 'action_plan.json')\n"
        "with open(plan_path, 'w') as f:\n"
        "    json.dump({\n"
        "        'project': result['project']['name'],\n"
        "        'timestamp': result['timestamp'],\n"
        "        'summary': {\n"
        "            'source_files': result['project']['total_source_files'],\n"
        "            'lint_findings': result['lint']['total_findings'],\n"
        "            'under_specified': result['specification']['under_specified_count'],\n"
        "            'action_count': len(result['action_plan']),\n"
        "        },\n"
        "        'action_plan': result['action_plan'],\n"
        "    }, f, indent=2)\n"
        "print(f'Action plan saved: {plan_path}')\n"
        "\n"
        "# Zip for download\n"
        "zip_path = '/content/lintgate_analysis.zip'\n"
        "!cd {PROJECT_DIR} && zip -r {zip_path} .lintgate/full_analysis.json .lintgate/action_plan.json -x '*.DS_Store'\n"
        "\n"
        "if os.path.exists(zip_path):\n"
        "    size_mb = os.path.getsize(zip_path) / 1024 / 1024\n"
        "    print(f'\\nDownload ready: {size_mb:.1f} MB')\n"
        "    try:\n"
        "        from google.colab import files\n"
        "        files.download(zip_path)\n"
        "        print('Download started!')\n"
        "    except ImportError:\n"
        "        print(f'Not in Colab. Results at: {zip_path}')\n"
        "else:\n"
        "    print('No zip created.')\n"
        "\n"
        "print(f'\\n--- HOW TO USE ---')\n"
        "print(f'Pass the action_plan.json to your LLM coding agent with:')\n"
        "print(f'  \"Implement the fixes in this action plan, starting from rank 1.\"')\n"
        "print(f'  \"Each action has priority, dependencies, and specific instructions.\"')\n"
        f"print(f'  \"Apply results to: {local_project_path}\"')"
    )

    mode_note = (
        "**Mode**: Self-analysis (LintGate analyzing itself)"
        if is_self_analysis
        else f"**Mode**: External project analysis\n- **Tool**: LintGate (`{lintgate_url}`)\n- **Target**: `{repo_url}`"
    )

    cells = [
        _md(
            "# LintGate Full Analysis\n"
            "\n"
            "**Auto-generated** comprehensive project analysis notebook.\n"
            "Runs the entire LintGate analysis pipeline offline and produces\n"
            "a portable JSON artifact with a prioritized action plan.\n"
            "\n"
            f"- **Target repo**: `{repo_url}`\n"
            f"- **Branch**: `{branch}`\n"
            f"- **Source dirs**: `{src_dirs_str}`\n"
            f"- **Mutation profiling**: `{mutation_flag}`\n"
            f"- {mode_note}\n"
            "\n"
            "**How to use:** Runtime > Run all. Download the zip at the end.\n"
            "Pass the `action_plan.json` to any LLM coding agent.\n"
            "\n"
            "The action plan is:\n"
            "- **Prioritized**: P0 blocking → P1 critical → P2 important → P3 improve\n"
            "- **Dependency-ordered**: lint fixes before spec work, tests before mutation profiling\n"
            "- **Actionable**: each item has specific tool commands and expected outcomes"
        ),
        _md("## Step 1: Install LintGate + Clone Target"),
        _code(install_cell),
        _md("## Step 2: Run Full Analysis"),
        _code(analysis_cell),
        _md("## Step 3: Review Action Plan"),
        _code(plan_cell),
        _md("## Step 4: Save & Download"),
        _code(save_cell),
    ]

    return {
        "cells": cells,
        "metadata": {
            "colab": {"provenance": [], "name": "LintGate Full Analysis"},
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
            "language_info": {"name": "python", "version": "3.11.0"},
        },
        "nbformat": 4,
        "nbformat_minor": 0,
    }

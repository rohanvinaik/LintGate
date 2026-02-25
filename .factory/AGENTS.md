# LintGate — Factory Droid Execution Context

> You are executing a specific GitHub issue against this codebase.
> Read this file before making any changes.

## Project Summary

LintGate is a Python MCP server (~34K LOC, ~95 Python files) providing real-time code quality supervision for AI coding agents. It exposes 60 MCP tools backed by 18 linters, organized into a ControlPlane with 6 parallel analysis channels.

## Build & Test

```bash
# Activate the virtual environment (ALWAYS do this first)
source .venv/bin/activate

# Run the full test suite
pytest tests/

# Run tests for a specific module
pytest tests/test_mutation_engine.py -v

# Verify the codebase can ship (full local gate stack)
python scripts/ship_main.py --preflight

# Lint check (ruff)
ruff check lintgate/ mcp_tools/ tests/

# Type check
ty check lintgate/ mcp_tools/
```

## Architecture Overview

```
mcp_server.py              ← MCP server entry point, registers tools
mcp_tools/                 ← MCP tool implementations (one file per domain)
  ├── mutation_tools.py    ← mutation_run_sampling, mutation_get_state, etc.
  ├── lint_tools.py        ← lint_files, lint_project, lint_fix
  ├── controlplane_tools.py ← controlplane_run, controlplane_get_details
  ├── quality_helpers.py   ← CI/quality setup artifact generation (god-module, see #99)
  └── ...                  ← ~15 tool modules total
lintgate/                  ← Core library
  ├── channels/            ← ControlPlane analysis channels (lint, test, git, behavior, structure, mutation)
  ├── controlplane/        ← ControlPlane engine, coherence, session memory, behavioral compass
  ├── mutation/            ← Mutation testing engine, state, prescriptions, decomposition
  ├── linters/             ← Linter integrations and custom checks
  │   └── performance_checks/ ← Algebra pipeline (purity, algebraic properties, manifest)
  ├── hooks/               ← PostToolUse / PreToolUse hook implementations
  └── ...                  ← config, state, telemetry, compass, theory
tests/                     ← pytest test suite (~1600 tests)
scripts/                   ← ship_main.py, validation scripts
.github/workflows/         ← CI workflows (tests, mutation, security, sonar, qlty, badges)
```

## Critical Conventions

### Backward Compatibility on Decomposition
When splitting a module into a package, ALWAYS create a compatibility shim at the old import path that re-exports all public names. Example:
```python
# old_module.py (becomes thin shim)
from old_module.new_submodule import PublicClass, public_function  # noqa: F401
```
This prevents import breakage across the codebase and in external consumers.

### MCP Tool Contracts
- Tool signatures are public API. Do not change parameter names or remove parameters.
- New fields in tool responses must be additive (never remove existing keys).
- If you add or remove an MCP tool, update the count in AGENTS.md and README.md.
- Source of truth for tool count: `grep -Rho "@mcp.tool()" mcp_server.py mcp_tools/*.py | wc -l`

### Mutation State Path (Critical — see #96)
- Canonical mutation state lives at the path resolved by `MUTATION_CACHE_DIR/state.json`
- ALL readers and writers of mutation state MUST use the same resolved path
- If you see `PERF_CACHE_DIR/mutation_state.json`, that is the OLD split-brain path — do not add new references to it

### Schema Versioning
- State files (mutation state, session memory, etc.) include a `schema_version` field
- Bump the version when changing the schema
- Always include a migration/compat loader for the previous version

## File Naming and Style

- Python files: `snake_case.py`
- Test files: `test_<module_name>.py` in `tests/`
- MCP tool files: `<domain>_tools.py` in `mcp_tools/`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Type hints required on all public function signatures
- Docstrings required on all public classes and functions

## Security and Boundaries

- NEVER commit secrets, API keys, or tokens
- NEVER modify `.claude/CLAUDE.md` or `.claude/rules/*` — those are maintained separately
- NEVER disable lint rules globally to hide issues
- NEVER auto-apply generated repairs without the issue explicitly requesting it
- Do not modify `setup.sh` or `integrate.sh` unless the issue specifically targets them
- Do not touch `pyproject.toml` dependency lists unless the issue specifically requires it

## Git Workflow

- Create a feature branch from `main`
- One logical change per commit
- Commit messages: imperative mood, concise subject line
- Run `pytest tests/` before every commit
- Run `ruff check` before every commit
- PR description should reference the GitHub issue number

## Common Gotchas

1. **Optional dependencies**: `libcst`, `mutmut`, `hypothesis` are optional. Code that uses them MUST have `try/except ImportError` guards with graceful fallback.
2. **Cache directories**: Multiple cache dirs exist (`MUTATION_CACHE_DIR`, `PERF_CACHE_DIR`, `LINTGATE_HOME`). Check `lintgate/config.py` for canonical path resolution before adding new cache paths.
3. **Test isolation**: Tests should not depend on global state or file system side effects. Use `tmp_path` fixtures.
4. **CI vs local divergence**: CI environments may not have all optional dependencies. If your change uses an optional dep, ensure the fallback path works.
5. **The `.hypothesis/` directory**: This is auto-generated by Hypothesis tests. It is gitignored. Do not track it.
6. **Sonar coverage exclusions**: `lintgate/mutation/**` and `mcp_tools/mutation_tools.py` have temporary coverage exclusions. New code in these paths needs targeted tests to eventually remove the exclusions (see #96 P1).

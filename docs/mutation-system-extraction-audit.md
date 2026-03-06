# Mutation System Extraction Audit

## Scope
Full extraction of the legacy mutation subsystem from active runtime paths, with archival preservation.

## Active Runtime Changes

### 1) Removed active mutation runtime dependencies
- `lintgate/linters/performance_checks/manifest.py`
  - Removed mutation-state manager import and mutation-mtime cache invalidation logic.
  - Manifest cache version bumped to `v3`.
- `lintgate/linters/performance_checks/properties.py`
  - Removed mutation gate logic and mutation-prescription coupling.
  - Kept backward-compatible parameters as inert shims.
- `mcp_tools/performance_tools.py`
  - Mutation-prescription generation path now explicit archived notice.
  - `prefer_mutation_hotspots` retained as compatibility flag but ignored.
- `mcp_tools/test_effectiveness_tools.py`
  - Removed mutation CI parser imports and mutation-backed calibration tool registration.
  - Preserved stable output keys with archived placeholders:
    - `mutation_ci_context`
    - `mutation_hotspots`
- `lintgate/controlplane/cross_channel.py`
  - Reworked coherence from `performance + mutation + test_effectiveness` to
    `performance + test_effectiveness`.
- `lintgate/convergence/integration.py`
  - Removed `TYPE_CHECKING` dependency on mutation decomposition types.

### 2) Removed mutation phase semantics from bootstrap
- `lintgate/orchestration/bootstrap_state.py`
  - Removed `"mutation"` from phase list.
  - Added state migration for legacy persisted states (`phase="mutation"` -> `"contracts"`).
  - Drops legacy artifact field `mutation_output_path` during load.
- `lintgate/orchestration/bootstrap_pipeline.py`
  - Removed mutation phase execution and placeholders.
  - Pipeline now completes after contracts phase.

### 3) Updated tool-surface and count metadata
- Current MCP tool decorator count: **74**
- Updated count references in:
  - `AGENTS.md`
  - `README.md`
  - `SKILL.md`
  - `docs/reference.md`
  - `mcp_server.py` instructions
- Removed mutation tool entries and `calibrate_assertion_weights` from active reference tables.

## Archived Components

Moved to `archive/mutation_system/`:
- `lintgate/mutation/` package
- `lintgate/channels/mutation_channel.py`
- `mcp_tools/mutation_tools.py`
- Scripts:
  - `mutation_ab_test.py`
  - `mutation_calibration_report.py`
  - `parse_mutation_stats.py`
  - `validate_mutation_policy.py`
- CI/badge artifacts:
  - `.github/workflows/mutation.yml`
  - `.github/badges/mutation.svg`
- Mutation-specific and mutation-dependent tests (moved under `archive/mutation_system/tests/`)

## Verification
- Python compile checks passed for all touched active modules.
- Targeted regression suites passed (`175` tests in one batch + `89` controlplane helper tests).
- Runtime smoke checks confirm:
  - No mutation MCP tools registered.
  - No `calibrate_assertion_weights` tool registered.
  - MCP instructions and doc counts aligned to `74` tools.

## Residual Mentions (Non-runtime)
Some files still use the word "mutation" in non-subsystem contexts (e.g., purity terminology,
system mutation guard wording, historical comments, docs). These do not execute mutation-engine code.

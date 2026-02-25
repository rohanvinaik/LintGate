# Issue #96 Droid Queue

Parent: https://github.com/rohanvinaik/LintGate/issues/96

## Phase plan

### Phase P0 (can run in parallel)
- #107 — Canonical mutation state path contract across runtime readers/writers
- #108 — Required-check dependency profile contract gate
- #109 — Blocking tracked-artifact hygiene gate

### Phase P1 (sequential due shared `sonar-project.properties`)
- #110 — Retire Sonar exclusion for `mcp_tools/mutation_tools.py`
- #111 — Retire Sonar exclusion for `lintgate/mutation/**`

### Phase P2 (can run in parallel after P1)
- #112 — Qlty transient setup resilience (retry/backoff + diagnostics)
- #113 — `ship_main` required-check telemetry classification

## Suggested execution order
1. Run #107, #108, #109 in parallel (separate PRs).
2. Merge all P0 PRs.
3. Run #110.
4. Merge #110.
5. Run #111.
6. Merge #111.
7. Run #112 and #113 in parallel.
8. Merge both P2 PRs.

## Notes
- #96 P1 check-topology normalization is partially covered already by `lintgate/quality_infra.py` + `tests/test_quality_infra.py` gate-contract parity checks.
- Remaining #96 item not yet split here: explicit gate-scope policy documentation issue (source/test vs scratch/generated).

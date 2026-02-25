# Issue #114 Droid Queue

Parent: https://github.com/rohanvinaik/LintGate/issues/114

## Scope note
`#99` verification/proof-of-concept is intentionally excluded from this queue.

## Audit status (as of 2026-02-25)

Closed:
- #115 — SurvivorSite schema + migration compatibility
- #117 — MCP exposure of `survivor_sites`
- #118 — Deterministic decomposition-axis clustering
- #123 — Non-blocking hook enqueue integration

Open (partial, carryover):
- #116 — Engine population of `survivor_sites` (operator metadata + determinism guard pending)
- #119 — Tool/channel decomposition-plan integration (channel runtime bug on axes path pending)
- #120 — Prescription -> `test_skeleton_hints` bridge (structured/per-axis hints pending)
- #121 — Covered-category subtraction + telemetry split (`mutants_skipped_covered` emission pending)
- #122 — Single-call refactor-loop delta (explicit sampled/profiled mode routing pending)

## New addendum issues

- #124 — Add additive `signal_quality` state schema and MCP exposure
- #125 — Deterministic `sampled_low` / `sampled_high` classification in engine
- #126 — Signal-quality-aware gate behavior in mutation channel
- #127 — Reproducible repository-level threshold calibration metadata
- #128 — Telemetry taxonomy instrumentation (`integrity/efficiency/signal_quality/actionability`)

## Recommended execution order

1. #119
2. #121
3. #116
4. #120
5. #122
6. #124
7. #125
8. #126
9. Parallel: #127 + #128

## Queue rules

- Keep one issue per PR; no cross-issue bundling.
- Use explicit adversarial acceptance tests for every new behavior path.
- For schema edits, require migration assertions and additive output checks.
- For telemetry edits, require increment assertions (not only field-presence checks).

# Issue #143 Small-Agent Queue

Parent: https://github.com/rohanvinaik/LintGate/issues/143

## Child issues

### Gap A — Scoped analysis
- #144 — Add explicit `controlplane_run` scoping contract (`scope`, `files`)
- #145 — Add `max_findings` truncation + scoped compact metadata

### Gap D — Stuck/cycle intervention
- #146 — Deterministic cycle-detection core
- #147 — Hook integration for cycle interventions + escalation

### Gap B — Adaptive startup orchestration
- #148 — Intent-aware `getting_started` workflows
- #149 — `tool_applicability_guide` + cadence/anti-pattern guidance

### Gap C — Remediation routing
- #150 — Finding-to-next_action deterministic router
- #151 — Integrate `next_action`/`remediation_sequence` into details tools

### Gap E — Output scaling
- #152 — `output_budget` modes (`minimal|standard|detailed`)
- #153 — Auto-budget selection from model risk profiles

### Gap F — Disposition enforcement
- #154 — Disposition enforcer core + config schema
- #155 — Hook advisory nudges + ignore-stop behavior

## Recommended execution order

1. #144
2. #145
3. #146
4. #147
5. #148
6. #149
7. #150
8. #151
9. #152
10. #153
11. #154
12. #155

## Small-agent execution rules

- One issue -> one PR -> merge before dependent issues.
- Keep output contracts explicit and test required keys/types, not only presence.
- Include at least one adversarial test that exercises the exact new fallback/error branch.
- For hook behavior, assert rate limits and non-blocking output semantics.
- For telemetry/counters, test increment and non-overlap semantics, not only schema fields.
- For optional dependencies and model-profile lookups, require deterministic fallback to safe defaults.

## Parent closure condition

Close #143 when #144-#155 are complete and the parent DoD is met across scoped analysis, adaptive workflows, remediation routing, cycle intervention, output scaling, and disposition enforcement.

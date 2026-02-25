# Issue #129 Small-Agent Queue

Parent: https://github.com/rohanvinaik/LintGate/issues/129

## Child issues

- #130 — NSIL snapshot schema + deterministic compact serializers
- #131 — State projections + `nsil_state_snapshot` MCP tool
- #132 — Local runtime adapter protocol + deterministic discovery
- #133 — Ollama adapter implementation
- #134 — vLLM adapter implementation (optional-dependency-safe)
- #135 — Policy grammar compiler core
- #136 — Grammar wiring into vLLM adapter + rejection fixtures
- #137 — Action propose→verify core
- #138 — `nsil_verify_action` MCP tool + repair suggestions
- #139 — Training example schema + extractors
- #140 — Reward/curriculum + `nsil_extract_training_data`
- #141 — Eval harness Tier0/Tier1 baseline
- #142 — Eval harness Tier2/Tier3 + `nsil_benchmark`

## Recommended execution order

1. #130
2. #131
3. #132
4. Parallel: #133 + #134 + #135
5. Parallel: #136 + #137
6. Parallel: #138 + #139 + #141
7. #140
8. #142

## Rules for small coding agents

- Keep one issue per PR and one deliverable per issue.
- Do not run dependent issues before predecessors are merged.
- Add at least one adversarial test that executes the exact new branch (not only happy-path shape checks).
- For tool/schema outputs, assert exact required keys/types in tests.
- For fallback behavior, require explicit markers in response payloads (for example `unsupported_tier`, `truncated_fields`).
- For metrics/counters, assert counter increment semantics under fixture events, not only field presence.
- For optional dependencies, require import-time resilience tests plus clear runtime error behavior.

## Completion condition for parent #129

Close #129 when #130-#142 are complete and parent DoD criteria are verified (state budget, adapter support, grammar constraints, verify-loop behavior, training extraction, and tiered benchmark delta reporting).

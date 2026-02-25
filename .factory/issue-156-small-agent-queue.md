# Issue #156 Small-Agent Queue

Parent: https://github.com/rohanvinaik/LintGate/issues/156

## Child issues

### Phase 3 — Unified delivery abstraction
- #157 — Delivery protocol + host routing metadata
- #158 — Delivery adapters + deterministic fallback routing
- #159 — Hook/MCP integration + proactive behavior status push

### Phase 4 — Graduated authority model
- #160 — Authority escalation core
- #161 — Authority integration in behavior/arbitration/delivery

### Phase 1 — Successful pattern repertoire
- #162 — Resolution repertoire capture + persistence
- #163 — Proven resolution surfacing in findings/details

### Phase 5 — Compliance feedback loop
- #164 — Structured nudge outcomes + compliance classification
- #165 — Compliance-driven escalation/cadence/reinforcement

### Phase 2 — Signal significance discrimination
- #166 — Signal source decomposition + behavior baselines
- #167 — Confidence modulation from source attribution

### Phase 6 — Cross-session transfer protocol
- #168 — Session transfer packet generation + persistence
- #169 — Session-start preload for repertoire/authority/compliance
- #170 — MCP observability for transfer/delivery health

## Recommended execution order

1. #157
2. #158
3. #159
4. #160
5. #161
6. #162
7. #163
8. #164
9. #165
10. #166
11. #167
12. #168
13. #169
14. #170

## Optional safe parallel windows

- After #157: run #160 and #162 in parallel with #158 if separate PRs.
- After #165: run #166 and #168 in parallel.
- After #168: run #169 and #170 in parallel.

## Small-agent execution rules

- One issue -> one PR -> merge before dependents.
- Keep output schemas explicit and test required keys/types.
- Include at least one adversarial test for the exact fallback/error branch.
- For hook changes, assert non-blocking behavior and rate-limit semantics.
- For persistence changes, assert fail-open behavior on corrupt files.
- For confidence/escalation math, assert deterministic results for fixed fixtures.

## Parent closure condition

Close #156 when #157-#170 are complete and parent DoD is verified across delivery portability, authority escalation, repertoire integration, compliance adaptation, significance decomposition, and session transfer.

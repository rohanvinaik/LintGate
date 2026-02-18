# LintGate Context

## Mission

- Keep feedback loops tight between generated code and validated code quality.
- Prefer deterministic checks and explicit diagnostics over ambiguous heuristics.
- Preserve graceful degradation when optional tooling is unavailable.

## Guardrails

- DO NOT disable lint channels globally to hide regressions.
- DO NOT auto-apply generated repairs without explicit acceptance.
- MUST keep hook and MCP outputs machine-readable and stable for downstream consumers.
- MUST preserve backward-compatible MCP tool contracts unless versioned intentionally.

## Enforceable Rules

LINTGATE_RULE: require_regex=^# LintGate$; path=README.md; severity=informational; message=README should keep canonical project title
LINTGATE_RULE: forbid_regex=\\bDONT_SHIP_SECRETS\\b; path=**/*.py; severity=blocking; message=Remove secret-handling placeholder markers before commit
LINTGATE_RULE: forbid_regex=disable_lint_channels_globally; path=**/*.md; severity=warning; message=DO NOT disable lint channels globally to hide regressions.
LINTGATE_RULE: forbid_regex=auto_apply_generated_repairs_without_acceptance; path=**/*.md; severity=warning; message=DO NOT auto-apply generated repairs without explicit acceptance.

## Debt Tracking Policy

- Known structural debt should be tracked in .claude/lintgate.yaml exemptions with ticket references.
- Exemptions should target specific files and codes instead of global severity downgrades.
- New exemptions require a concrete rationale and a remediation ticket.

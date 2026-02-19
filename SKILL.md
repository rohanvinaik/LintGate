# LintGate

Real-time quality supervision for AI-generated code. Fires on every Write, Edit, and Bash via PostToolUse hooks. 28 MCP tools for on-demand analysis.

## First Steps

1. **Orient**: `build_theory_pack` — get the project's theory digest (~500-1500 tokens)
2. **Check**: `controlplane_run` — full supervision mesh (lint + tests + deps + git + behavior)
3. **Reflect**: `behavior_precheck` — state your constraints before acting, register predictions

## Key Concept

LintGate is not just a linter. It supervises the agent's *reasoning strategy*, not just its code output. Behavioral drift detection catches approach cycling, failure amnesia, and verification debt. Theory extraction grounds findings in the project's own stated values. The Architecture of Inquiry closes the loop — behavioral discoveries flow back into the project's living semantic model.

## Setup

```bash
bash setup.sh    # venv, install, hook, MCP config, agent integration — one command
```

This detects which LLM agents are on the system and generates config files at the appropriate integration depth.

## Reference

- **AGENTS.md** — tool reference + self-integration for all agents (28 tools by cognitive mode)
- **`.claude/CLAUDE.md`** — Claude Code cognitive context: epistemic state, dispositions, guardrails, managed sections for living context. This is the deepest integration — behavioral discoveries flow back as patches.
- `.claude/rules/inquiry.md` — Architecture of Inquiry protocol
- `docs/design.md` — full architecture and design philosophy

For Claude Code, all three files work together. For other agents, AGENTS.md is the primary entry point and `integrate.sh` generates an appropriate cognitive context file.

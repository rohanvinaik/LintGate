# LintGate

Proactive professional cognition stack for LLM coding agents. Fires on every Write, Edit, and Bash via PostToolUse hooks. 49 MCP tools for on-demand analysis.

## First Steps

1. **Orient**: `build_theory_pack` — get the project's theory digest (~500-1500 tokens)
2. **Check**: `controlplane_run` — 6-channel supervision mesh (lint + tests + deps + git + behavior + structure)
3. **Reflect**: `constraint_check` — state your constraints before acting; `prediction_register` — register falsifiable predictions

## Key Concept

LintGate is not just a linter. It supervises the agent's *reasoning strategy*, not just its code output. The ControlPlane runs 6 independent channels in parallel — code quality, test health, dependency integrity, git hygiene, behavioral drift detection, and codebase structural analysis — with a coherence engine that reads the agreement and disagreement across channels as diagnostic signal. Theory extraction grounds findings in the project's own stated values. The Architecture of Inquiry closes the loop — behavioral discoveries flow back into the project's living semantic model. Habit Mode manages the context window itself — detecting sustained execution phases and producing structured compaction snapshots that turn context loss into context refinement.

## Setup

```bash
bash setup.sh    # venv, install, hook, MCP config, agent integration — one command
```

This detects which LLM agents are on the system and generates config files at the appropriate integration depth.

## Reference

- **AGENTS.md** — tool reference + self-integration for all agents (49 tools by cognitive mode)
- **`.claude/CLAUDE.md`** — Claude Code cognitive context: epistemic state, dispositions, guardrails, managed sections for living context. This is the deepest integration — behavioral discoveries flow back as patches.
- `.claude/rules/inquiry.md` — Architecture of Inquiry protocol
- `docs/design.md` — full architecture and design philosophy

For Claude Code, all three files work together. For other agents, AGENTS.md is the primary entry point and `integrate.sh` generates an appropriate cognitive context file.

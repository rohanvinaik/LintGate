# Mutation System Archive

This directory contains the fully extracted legacy mutation subsystem and its direct tests/scripts.

## Why this exists
The mutation subsystem was removed from the active LintGate runtime to prevent hidden hooks,
automatic mutation runs, and mutmut trampoline side effects. The archived code is preserved here
for historical reference only.

## What was archived
- `lintgate/mutation/*` runtime package (engine, automation, policy, state, prescriptions, etc.)
- `lintgate/channels/mutation_channel.py`
- `mcp_tools/mutation_tools.py`
- mutation-specific utility scripts formerly under `scripts/`
- mutation-specific and mutation-dependent tests formerly under `tests/`

## Active runtime status
- No active `lintgate.mutation.*` imports remain in production code paths.
- Mutation MCP tools are not registered.
- Mutation channel is not registered in ControlPlane defaults.
- Bootstrap pipeline no longer has a mutation phase.
- Mutation-backed assertion calibration MCP entry point is removed.

## Note
The archive is intentionally inert. Nothing under this directory is imported or executed by default.

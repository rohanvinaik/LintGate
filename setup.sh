#!/usr/bin/env bash
# LintGate setup — installs, configures hook + MCP, verifies.
# Usage: bash setup.sh [--minimal]
#
# --minimal: Install only ruff + mcp (skip mypy, radon, bandit, vulture)
# Default:   Install full linter suite + mcp + dev dependencies
set -euo pipefail

LINTGATE_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$LINTGATE_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python3"
VENV_LINTGATE="$VENV_DIR/bin/lintgate"
VENV_MCP="$VENV_DIR/bin/lintgate-mcp"

MINIMAL=false
[[ "${1:-}" == "--minimal" ]] && MINIMAL=true

# JSON merge helper (preserves existing keys; repairs invalid JSON via backup).
ensure_json_file() {
    local path="$1"
    local kind="$2"
    if [ -f "$path" ]; then
        return 0
    fi
    mkdir -p "$(dirname "$path")"
    echo "{}" > "$path"
    echo "  Created $kind config: $path"
}

merge_claude_hook() {
    local path="$1"
    local hook_cmd="$2"
    "$VENV_PYTHON" - "$path" "$hook_cmd" << 'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
hook_cmd = sys.argv[2]

data = {}
recovered = False
backup_path = ""
if path.exists():
    try:
        raw = path.read_text()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        backup = path.with_suffix(path.suffix + ".lintgate.bak")
        path.rename(backup)
        recovered = True
        backup_path = str(backup)
        data = {}

hooks = data.get("hooks")
if not isinstance(hooks, dict):
    hooks = {}
    data["hooks"] = hooks

post = hooks.get("PostToolUse")
if not isinstance(post, list):
    post = []
    hooks["PostToolUse"] = post

found = False
updated = False
for entry in post:
    if not isinstance(entry, dict):
        continue
    entry_hooks = entry.get("hooks")
    if not isinstance(entry_hooks, list):
        continue
    for hook in entry_hooks:
        if not isinstance(hook, dict):
            continue
        if hook.get("type") != "command":
            continue
        command = hook.get("command")
        if command == hook_cmd:
            found = True
            continue
        if isinstance(command, str) and os.path.basename(command) == "lintgate":
            hook["command"] = hook_cmd
            found = True
            updated = True

if not found:
    post.append(
        {
            "matcher": "Write|Edit|MultiEdit|Bash",
            "hooks": [{"type": "command", "command": hook_cmd}],
        }
    )
    updated = True

path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(data, indent=2) + "\n")
if recovered:
    print(f"recovered:{backup_path}")
elif updated:
    print("updated")
else:
    print("unchanged")
PY
}

merge_mcp_server() {
    local path="$1"
    local mcp_cmd="$2"
    "$VENV_PYTHON" - "$path" "$mcp_cmd" << 'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
mcp_cmd = sys.argv[2]

data = {}
recovered = False
backup_path = ""
if path.exists():
    try:
        raw = path.read_text()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        backup = path.with_suffix(path.suffix + ".lintgate.bak")
        path.rename(backup)
        recovered = True
        backup_path = str(backup)
        data = {}

servers = data.get("mcpServers")
if not isinstance(servers, dict):
    servers = {}
    data["mcpServers"] = servers

desired = {
    "command": mcp_cmd,
    "args": [],
}
status = "unchanged"
if servers.get("lintgate") != desired:
    servers["lintgate"] = desired
    status = "updated"

path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(data, indent=2) + "\n")
if recovered:
    print(f"recovered:{backup_path}")
else:
    print(status)
PY
}

echo "=== LintGate Setup ==="
echo "Project: $LINTGATE_DIR"
echo "Mode:    $([ "$MINIMAL" = true ] && echo 'minimal' || echo 'full')"
echo ""

# ── Step 1: venv ────────────────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating venv..."
    if command -v uv &>/dev/null; then
        uv venv "$VENV_DIR"
    else
        python3 -m venv "$VENV_DIR"
    fi
    echo "  Created $VENV_DIR"
else
    echo "  Venv exists: $VENV_DIR"
fi

# ── Step 2: Install ─────────────────────────────────────────────────────
echo ""
echo "Installing LintGate..."
if command -v uv &>/dev/null; then
    if [ "$MINIMAL" = true ]; then
        uv pip install --python "$VENV_PYTHON" -e "$LINTGATE_DIR[mcp]" --quiet
        uv pip install --python "$VENV_PYTHON" ruff --quiet
    else
        uv pip install --python "$VENV_PYTHON" -e "$LINTGATE_DIR[dev]" --quiet
    fi
else
    if [ "$MINIMAL" = true ]; then
        "$VENV_PYTHON" -m pip install -e "$LINTGATE_DIR[mcp]" --quiet
        "$VENV_PYTHON" -m pip install ruff --quiet
    else
        "$VENV_PYTHON" -m pip install -e "$LINTGATE_DIR[dev]" --quiet
    fi
fi
echo "  Installed."

# ── Step 3: Verify MCP server loads ─────────────────────────────────────
echo ""
echo "Verifying MCP server..."
if [ ! -x "$VENV_MCP" ]; then
    echo "  WARNING: MCP entrypoint missing at $VENV_MCP"
    TOOL_COUNT="0"
else
    TOOL_COUNT=$("$VENV_PYTHON" -c "
from mcp_server import mcp
print(len(mcp._tool_manager._tools))
" 2>/dev/null || echo "0")
fi

if [ "$TOOL_COUNT" -gt 0 ]; then
    echo "  MCP server OK: $TOOL_COUNT tools registered ($VENV_MCP)"
else
    echo "  WARNING: MCP server failed to load. Check mcp package installation."
fi

# ── Step 4: Configure PostToolUse hook ──────────────────────────────────
echo ""
SETTINGS_DIR="$HOME/.claude"
SETTINGS_FILE="$SETTINGS_DIR/settings.json"
ensure_json_file "$SETTINGS_FILE" "Claude"
HOOK_STATUS="$(merge_claude_hook "$SETTINGS_FILE" "$VENV_LINTGATE")"
case "$HOOK_STATUS" in
    recovered:*) echo "  WARNING: repaired invalid settings JSON (backup: ${HOOK_STATUS#recovered:})" ;;
    updated) echo "  Updated PostToolUse hook in $SETTINGS_FILE" ;;
    unchanged) echo "  PostToolUse hook already configured in $SETTINGS_FILE" ;;
    *) echo "  Updated PostToolUse hook in $SETTINGS_FILE" ;;
esac

# ── Step 5: Configure MCP server ────────────────────────────────────────
echo ""
MCP_CONFIG="$HOME/.mcp.json"
ensure_json_file "$MCP_CONFIG" "MCP"
MCP_STATUS="$(merge_mcp_server "$MCP_CONFIG" "$VENV_MCP")"
case "$MCP_STATUS" in
    recovered:*) echo "  WARNING: repaired invalid MCP JSON (backup: ${MCP_STATUS#recovered:})" ;;
    updated) echo "  Updated MCP server in $MCP_CONFIG" ;;
    unchanged) echo "  MCP server already configured in $MCP_CONFIG" ;;
    *) echo "  Updated MCP server in $MCP_CONFIG" ;;
esac

# ── Step 6: Also create project-level .mcp.json ────────────────────────
PROJECT_MCP="$LINTGATE_DIR/.mcp.json"
ensure_json_file "$PROJECT_MCP" "project MCP"
PROJECT_MCP_STATUS="$(merge_mcp_server "$PROJECT_MCP" "$VENV_MCP")"
case "$PROJECT_MCP_STATUS" in
    recovered:*) echo "  WARNING: repaired invalid project MCP JSON (backup: ${PROJECT_MCP_STATUS#recovered:})" ;;
    updated) echo "  Updated project .mcp.json" ;;
    unchanged) echo "  Project .mcp.json already configured" ;;
    *) echo "  Updated project .mcp.json" ;;
esac

# ── Step 7: Bootstrap context files if missing ────────────────────────
REQUIRED_BOOTSTRAP_FILES=(
    "$LINTGATE_DIR/.claude/CLAUDE.md"
    "$LINTGATE_DIR/AGENTS.md"
    "$LINTGATE_DIR/.claude/rules/inquiry.md"
    "$LINTGATE_DIR/.claude/rules/theory.md"
)
MISSING_BOOTSTRAP_FILES=()
for f in "${REQUIRED_BOOTSTRAP_FILES[@]}"; do
    if [ ! -f "$f" ]; then
        MISSING_BOOTSTRAP_FILES+=("$f")
    fi
done

if [ ${#MISSING_BOOTSTRAP_FILES[@]} -gt 0 ]; then
    echo ""
    echo "Bootstrapping context files (CLAUDE.md, AGENTS.md, inquiry.md, theory.md)..."
    for f in "${MISSING_BOOTSTRAP_FILES[@]}"; do
        echo "  missing: $f"
    done
    "$VENV_PYTHON" - "$LINTGATE_DIR" <<'PY' 2>/dev/null || echo "  WARNING: Bootstrap failed. Run bootstrap_context_files manually via MCP."
import sys
from lintgate.context_bootstrap import bootstrap_context_files

project_root = sys.argv[1]
result = bootstrap_context_files(project_root, write=True)
for f in result.get("files", []):
    print(f"  {f['status']}: {f['relative_path']} ({f['line_count']} lines)")
PY
else
    echo ""
    echo "  Context files exist — skipping bootstrap."
fi

# ── Step 8: Agent integration ─────────────────────────────────────────
echo ""
echo "Detecting LLM coding agents and generating config files..."
bash "$LINTGATE_DIR/integrate.sh"

# ── Summary ─────────────────────────────────────────────────────────────
echo ""
echo "=== Setup Complete ==="
echo ""
echo "PostToolUse hook: $VENV_LINTGATE"
echo "MCP server:       $VENV_MCP"
echo "MCP tools:        $TOOL_COUNT"
echo ""
echo "The hook fires automatically on Write/Edit/Bash."
echo "MCP tools are available for on-demand analysis."
echo "Agent config files point all detected agents to AGENTS.md."
echo ""
echo "Quick test:"
echo "  $VENV_LINTGATE <<< '{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"test.py\"},\"tool_output\":\"ok\",\"cwd\":\"'$LINTGATE_DIR'\"}'"

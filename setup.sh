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
MCP_SERVER="$LINTGATE_DIR/mcp_server.py"

MINIMAL=false
[[ "${1:-}" == "--minimal" ]] && MINIMAL=true

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
        uv pip install -e "$LINTGATE_DIR[mcp]" --quiet
        uv pip install ruff --quiet
    else
        uv pip install -e "$LINTGATE_DIR[dev]" --quiet
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
TOOL_COUNT=$("$VENV_PYTHON" -c "
import sys
sys.path.insert(0, '$LINTGATE_DIR')
from mcp_server import mcp
print(len(mcp._tool_manager._tools))
" 2>/dev/null || echo "0")

if [ "$TOOL_COUNT" -gt 0 ]; then
    echo "  MCP server OK: $TOOL_COUNT tools registered"
else
    echo "  WARNING: MCP server failed to load. Check mcp package installation."
fi

# ── Step 4: Configure PostToolUse hook ──────────────────────────────────
echo ""
SETTINGS_DIR="$HOME/.claude"
SETTINGS_FILE="$SETTINGS_DIR/settings.json"

if [ -f "$SETTINGS_FILE" ]; then
    # Check if lintgate hook already configured
    if grep -q "$VENV_LINTGATE" "$SETTINGS_FILE" 2>/dev/null; then
        echo "  PostToolUse hook already configured in $SETTINGS_FILE"
    else
        echo "  PostToolUse hook not found in $SETTINGS_FILE"
        echo "  Add this to your hooks configuration:"
        echo ""
        echo '  "PostToolUse": [{'
        echo '    "matcher": "Write|Edit|MultiEdit|Bash",'
        echo '    "hooks": [{'
        echo '      "type": "command",'
        echo "      \"command\": \"$VENV_LINTGATE\""
        echo '    }]'
        echo '  }]'
        echo ""
    fi
else
    echo "  No ~/.claude/settings.json found."
    echo "  Creating with PostToolUse hook..."
    mkdir -p "$SETTINGS_DIR"
    cat > "$SETTINGS_FILE" << SETTINGS_EOF
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Write|Edit|MultiEdit|Bash",
      "hooks": [{
        "type": "command",
        "command": "$VENV_LINTGATE"
      }]
    }]
  }
}
SETTINGS_EOF
    echo "  Created $SETTINGS_FILE"
fi

# ── Step 5: Configure MCP server ────────────────────────────────────────
echo ""
MCP_CONFIG="$HOME/.mcp.json"

if [ -f "$MCP_CONFIG" ]; then
    if grep -q "lintgate" "$MCP_CONFIG" 2>/dev/null; then
        echo "  MCP server already configured in $MCP_CONFIG"
    else
        echo "  LintGate not found in $MCP_CONFIG"
        echo "  Add this to your mcpServers:"
        echo ""
        echo "  \"lintgate\": {"
        echo "    \"command\": \"$VENV_PYTHON\","
        echo "    \"args\": [\"$MCP_SERVER\"]"
        echo "  }"
        echo ""
    fi
else
    echo "  No ~/.mcp.json found. Creating..."
    cat > "$MCP_CONFIG" << MCP_EOF
{
  "mcpServers": {
    "lintgate": {
      "command": "$VENV_PYTHON",
      "args": ["$MCP_SERVER"]
    }
  }
}
MCP_EOF
    echo "  Created $MCP_CONFIG"
fi

# ── Step 6: Also create project-level .mcp.json ────────────────────────
PROJECT_MCP="$LINTGATE_DIR/.mcp.json"
if [ ! -f "$PROJECT_MCP" ]; then
    cat > "$PROJECT_MCP" << PMCP_EOF
{
  "mcpServers": {
    "lintgate": {
      "command": "$VENV_PYTHON",
      "args": ["$MCP_SERVER"]
    }
  }
}
PMCP_EOF
    echo "  Created project .mcp.json"
fi

# ── Step 7: Bootstrap context files if missing ────────────────────────
CLAUDE_MD="$LINTGATE_DIR/.claude/CLAUDE.md"
if [ ! -f "$CLAUDE_MD" ]; then
    echo ""
    echo "Bootstrapping context files (CLAUDE.md, AGENTS.md, inquiry.md, theory.md)..."
    "$VENV_PYTHON" -c "
import sys
sys.path.insert(0, '$LINTGATE_DIR')
from lintgate.context_bootstrap import bootstrap_context_files
result = bootstrap_context_files('$LINTGATE_DIR', write=True)
for f in result.get('files', []):
    print(f'  {f[\"status\"]}: {f[\"relative_path\"]} ({f[\"line_count\"]} lines)')
" 2>/dev/null || echo "  WARNING: Bootstrap failed. Run bootstrap_context_files manually via MCP."
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
echo "MCP server:       $VENV_PYTHON $MCP_SERVER"
echo "MCP tools:        $TOOL_COUNT"
echo ""
echo "The hook fires automatically on Write/Edit/Bash."
echo "MCP tools are available for on-demand analysis."
echo "Agent config files point all detected agents to AGENTS.md."
echo ""
echo "Quick test:"
echo "  $VENV_LINTGATE <<< '{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"test.py\"},\"tool_output\":\"ok\",\"cwd\":\"'$LINTGATE_DIR'\"}'"

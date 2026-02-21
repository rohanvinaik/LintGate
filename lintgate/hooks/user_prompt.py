"""UserPromptSubmit hook — mode indicator and transition classification.

Fires when the user submits a prompt. Injects the current cognitive mode
as a system message prefix so the agent knows its operating context.

Protocol: stdin JSON → stdout JSON, exit 0 always.
"""

from __future__ import annotations

import json
import sys
from typing import Any

# Keywords that suggest the user wants theory exploration
_THEORY_KEYWORDS = frozenset(
    {
        "theory",
        "compass",
        "explore",
        "understand",
        "why",
        "philosophy",
        "principle",
        "architecture",
    }
)


def _load_mode(project_root: str) -> str:
    """Load current cognitive mode from session memory."""
    try:
        from lintgate.controlplane.session_memory import get_or_create_session

        session = get_or_create_session(project_root)
        mode = session.behavior_compass.get("mode_state", {}).get("current", "normal")
        return str(mode or "normal")
    except Exception:
        return "normal"


def handle(data: dict[str, Any]) -> dict[str, Any]:
    """Process UserPromptSubmit event."""
    project_root = data.get("cwd", ".")
    mode = _load_mode(project_root)
    user_message = data.get("userMessage", data.get("message", ""))
    if isinstance(user_message, dict):
        user_message = user_message.get("content", "")

    msg_lower = str(user_message).lower()
    mode_hint = ""
    if any(kw in msg_lower for kw in _THEORY_KEYWORDS):
        mode_hint = " (theory-relevant prompt detected)"

    system_message = ""
    if mode_hint:
        system_message = f"[Compass] Mode: {mode}{mode_hint}"
    elif mode != "normal":
        system_message = f"[Compass] Mode: {mode}"

    return {
        "continue": True,
        "systemMessage": system_message,
    }


def main() -> None:
    data = json.loads(sys.stdin.read())
    result = handle(data)
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()

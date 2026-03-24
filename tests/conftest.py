"""Pytest bootstrap for local package imports.

Ensures repo source wins over any installed site-packages copies.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _module_from_repo(module: object) -> bool:
    """Return True if a loaded module resolves inside the repo root."""
    file_path = getattr(module, "__file__", None)
    if not file_path:
        return True
    try:
        return Path(file_path).resolve().is_relative_to(_ROOT)
    except Exception:
        return False


# Force repo root to highest precedence even when pytest prepends tests/.
sys.path = [p for p in sys.path if p != str(_ROOT)]
sys.path.insert(0, str(_ROOT))

# Evict stale installed modules so imports resolve from this checkout.
for name, module in list(sys.modules.items()):
    if (
        (
            name == "mcp_server"
            or name == "mcp_tools"
            or name.startswith(("mcp_tools.", "lintgate."))
        )
        and module is not None
        and not _module_from_repo(module)
    ):
        sys.modules.pop(name, None)


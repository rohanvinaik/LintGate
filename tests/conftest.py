"""Pytest bootstrap for local package imports.

Allows `pytest` to run from the repo root without requiring an explicit
PYTHONPATH export.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

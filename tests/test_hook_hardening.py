"""Tests for hook hardening — persistence fidelity and signal decay."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from lintgate.compass import CompassAxis, CompassClaim, CompassState, compute_staleness
from lintgate.compass_io import load_compass, save_compass

if TYPE_CHECKING:
    from pathlib import Path


def test_compass_persistence_fidelity(tmp_path: Path) -> None:
    """Verify that CompassState round-trips through disk with fidelity."""
    project_root = str(tmp_path)
    axis = CompassAxis(
        name="problem",
        claims=[CompassClaim(text="Test claim", source="test_file:10", confidence=0.9)],
        summary="Test summary",
        depth=1,
    )
    state = CompassState(axes={"problem": axis})

    # Save
    path = save_compass(project_root, state)
    assert path.exists()
    assert state.forged_at > 0

    # Load
    loaded = load_compass(project_root)
    assert loaded is not None
    assert loaded.version == state.version
    assert "problem" in loaded.axes
    assert loaded.axes["problem"].claims[0].text == "Test claim"
    assert loaded.axes["problem"].depth == 1
    assert loaded.forged_at == state.forged_at


def test_refuses_to_save_empty_compass(tmp_path: Path) -> None:
    """save_compass should raise ValueError if axes are missing (schema hardening)."""
    import pytest

    state = CompassState(axes={})
    with pytest.raises(ValueError, match="Refusing to save empty CompassState"):
        save_compass(str(tmp_path), state)


def test_staleness_and_decay_logic() -> None:
    """Verify compute_staleness logic for signal decay simulation."""
    now = time.time()

    # Just forged
    state_new = CompassState(forged_at=now)
    assert compute_staleness(state_new, max_age_hours=24) < 0.001

    # 12 hours old
    state_mid = CompassState(forged_at=now - (12 * 3600))
    # Close enough to 0.5
    assert 0.49 < compute_staleness(state_mid, max_age_hours=24) < 0.51

    # 24 hours old
    state_old = CompassState(forged_at=now - (24 * 3600))
    assert compute_staleness(state_old, max_age_hours=24) == 1.0

    # 48 hours old (capped at 1.0)
    state_ancient = CompassState(forged_at=now - (48 * 3600))
    assert compute_staleness(state_ancient, max_age_hours=24) == 1.0


def test_load_compass_handles_corrupt_data(tmp_path: Path) -> None:
    """load_compass should return None for corrupt or non-dict YAML."""
    project_root = str(tmp_path)
    path = tmp_path / ".claude" / "compass.yaml"
    path.parent.mkdir(parents=True)

    # Case 1: Not a dict
    path.write_text("Hello World")
    assert load_compass(project_root) is None

    # Case 2: Missing required axes (runtime validation)
    path.write_text("version: 1\naxes: {}")
    assert load_compass(project_root) is None

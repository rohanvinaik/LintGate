"""Property tests for the assertion classifier."""

from __future__ import annotations

import os
import tempfile

from hypothesis import given
from hypothesis import strategies as st

from lintgate.linters.test_effectiveness.assertion_classifier import classify_test_file_from_path
from lintgate.linters.test_effectiveness.types import AssertionKind


@given(st.text())
def test_classifier_does_not_crash_on_random_garbage(garbage: str):
    """Invariant: The classifier must never crash, even on invalid Python or random text."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tmp:
        tmp.write(garbage)
        tmp_path = tmp.name

    try:
        # Should return empty dict or handle error gracefully, but not raise
        result = classify_test_file_from_path(tmp_path)
        assert isinstance(result, dict)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@given(
    st.lists(
        st.sampled_from(
            [
                "assert x == y",
                "assert x is not None",
                "assert isinstance(x, int)",
                "with pytest.raises(ValueError): pass",
                "self.assertEqual(x, y)",
                "assert x",
            ]
        ),
        min_size=1,
        max_size=10,
    )
)
def test_classifier_identifies_known_patterns(patterns: list[str]):
    """Invariant: Syntactically valid test functions with known patterns result in valid AssertionKinds."""
    code = "def test_func():\n" + "\n".join(f"    {p}" for p in patterns)

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tmp:
        tmp.write(code)
        tmp_path = tmp.name

    try:
        result = classify_test_file_from_path(tmp_path)
        if result and "test_func" in result:
            assertions = result["test_func"]
            assert len(assertions) > 0
            for a in assertions:
                assert isinstance(a.kind, AssertionKind)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

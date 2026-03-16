"""Backward-compatibility shim — module moved to lintgate.specification.prescriptive.synthesis.

This file re-exports all public names from the new location.
Remove this shim once all dependents have been updated.
"""

from lintgate.specification.prescriptive.synthesis import (  # noqa: F401
    PatternMatch,
    SynthesisResult,
    synthesize_body,
)

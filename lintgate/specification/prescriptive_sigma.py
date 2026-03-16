"""Backward-compatibility shim — module moved to lintgate.specification.prescriptive.sigma.

This file re-exports all public names from the new location.
Remove this shim once all dependents have been updated.
"""

from lintgate.specification.prescriptive.sigma import (  # noqa: F401
    compute_convergence_signal,
    estimate_prescriptive_sigma,
)

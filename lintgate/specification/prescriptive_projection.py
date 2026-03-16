"""Backward-compatibility shim — module moved to lintgate.specification.prescriptive.projection.

This file re-exports all public names from the new location.
Remove this shim once all dependents have been updated.
"""

from lintgate.specification.prescriptive.projection import (  # noqa: F401
    FunctionProjection,
    build_projection_from_ledger,
    load_projection,
    load_single_projection,
    save_projection,
)

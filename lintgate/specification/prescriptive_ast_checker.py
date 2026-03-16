"""Backward-compatibility shim — module moved to lintgate.specification.prescriptive.ast_checker.

This file re-exports all public names from the new location.
Remove this shim once all dependents have been updated.
"""

from lintgate.specification.prescriptive.ast_checker import (  # noqa: F401
    CheckResult,
    check_invariants_against_ast,
)

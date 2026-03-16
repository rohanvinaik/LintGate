"""Prescriptive specification package.

Re-exports all public names for backward compatibility.
Import from submodules directly for new code.
"""

from .claim_projection import project_claims  # noqa: F401
from .composer import PrescriptiveSpecComposer  # noqa: F401
from .persistence import (  # noqa: F401
    _SPEC_DIR,
    PrescriptiveWorkflowRecord,
    _target_hash,
    load_all_specs,
    load_spec,
    load_spec_index,
    load_workflow_record,
    save_spec,
    save_workflow_record,
    spec_coverage,
)
from .predicates import (  # noqa: F401
    Predicate,
    PredicateOp,
    compile_claim,
    pred_and,
    pred_custom,
    pred_eq,
    pred_gt,
    pred_gte,
    pred_lt,
    pred_neq,
    pred_no_raise,
    pred_not,
    pred_or,
    pred_param_count_lte,
    pred_pure,
    pred_raises,
    pred_returns_non_null,
    pred_true,
    pred_type,
)
from .resolver import (  # noqa: F401
    ResolvedTarget,
    _build_func_index,
    _find_function_at,
    _match_claims_to_symbols,
    _scan_pspec_stubs,
    resolve_targets,
)
from .spec import PrescriptiveSpec  # noqa: F401
from .types import (  # noqa: F401
    ForbiddenBehavior,
    GenerationConstraint,
    Invariant,
    RefinementObligation,
    StateTransition,
    StateVariable,
    TestObligation,
)

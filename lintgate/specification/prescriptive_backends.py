"""Backward-compatibility shim — module moved to lintgate.specification.prescriptive.backends.

This file re-exports all public names from the new location.
Remove this shim once all dependents have been updated.
"""

from lintgate.specification.prescriptive.adapter import (  # noqa: F401
    PrescriptiveAdapter,
)
from lintgate.specification.prescriptive.backends import (  # noqa: F401
    CompilationTargets,
    DistributedBackend,
    PureBackend,
    StatefulBackend,
    select_backend,
)
from lintgate.specification.prescriptive.gate import (  # noqa: F401
    SynthesisGateResult,
    WitnessRecord,
    check_synthesis_gate,
    generate_executable_witnesses,
)

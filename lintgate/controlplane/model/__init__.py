"""Model calibration subpackage.

Re-exports public API from submodules for backward-compatible imports.
Moved from lintgate/controlplane/model_*.py as STRUCT005 remediation.
"""

from __future__ import annotations

from .probe import (
    NEUTRAL_PRIOR,
    NEUTRAL_PRIOR_CONFIDENCE,
    PROBE_MAX_CONFIDENCE,
    PROBE_VERSION,
    SUPPORTED_PROBE_SETS,
    V1_PROBE_VERSION,
    build_profile_from_probe,
    compute_probe_validity,
    get_neutral_prior,
    get_probe_questions,
    get_probe_tasks,
    score_probe_responses,
)
from .probe_features import (
    extract_features_for_task,
)
from .probe_tasks import (
    PROBE_TASKS,
    BehavioralFeature,
    ProbeTask,
    TaskVariant,
)
from .profiles import (
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_STALENESS_DAYS,
    PROFILE_FORMAT_VERSION,
    ModelProfile,
    ModelProfileStore,
    apply_confidence_decay,
    apply_telemetry_update,
    get_profile,
    load_profiles,
    reset_profile,
    resolve_model_key,
    save_profiles,
    upsert_profile,
)

__all__ = [
    # probe
    "NEUTRAL_PRIOR",
    "NEUTRAL_PRIOR_CONFIDENCE",
    "PROBE_MAX_CONFIDENCE",
    "PROBE_VERSION",
    "SUPPORTED_PROBE_SETS",
    "V1_PROBE_VERSION",
    "build_profile_from_probe",
    "compute_probe_validity",
    "get_neutral_prior",
    "get_probe_questions",
    "get_probe_tasks",
    "score_probe_responses",
    # probe_features
    "extract_features_for_task",
    # probe_tasks
    "PROBE_TASKS",
    "BehavioralFeature",
    "ProbeTask",
    "TaskVariant",
    # profiles
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_STALENESS_DAYS",
    "PROFILE_FORMAT_VERSION",
    "ModelProfile",
    "ModelProfileStore",
    "apply_confidence_decay",
    "apply_telemetry_update",
    "get_profile",
    "load_profiles",
    "reset_profile",
    "resolve_model_key",
    "save_profiles",
    "upsert_profile",
]

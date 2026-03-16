"""Convergence aggregator: multi-lens evidence fusion for decomposition proposals."""

from .aggregator import (
    aggregate,
    classify_actionability,
)
from .aggregator_file_level import (
    aggregate_file,
    classify_file_actionability,
)
from .evidence import Actionability, ConvergenceResult, LensEvidence, LensKind
from .extraction_plan import ExtractionPlan, ExtractionStep, build_extraction_plan
from .projector import ProjectedOpportunity, project_post_extraction
from .synthesizer import OptimizationLandscape, synthesize_landscape

__all__ = [
    "LensEvidence",
    "LensKind",
    "ConvergenceResult",
    "Actionability",
    "aggregate",
    "aggregate_file",
    "classify_actionability",
    "classify_file_actionability",
    "ExtractionStep",
    "ExtractionPlan",
    "build_extraction_plan",
    "ProjectedOpportunity",
    "project_post_extraction",
    "OptimizationLandscape",
    "synthesize_landscape",
]

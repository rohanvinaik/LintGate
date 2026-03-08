"""Data types for the AST mutation engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import ast


class MutationCategory(str, Enum):
    """Semantic mutation category (§6.4 dispatch table)."""

    VALUE = "VALUE"
    SWAP = "SWAP"
    STATE = "STATE"
    BOUNDARY = "BOUNDARY"
    TYPE = "TYPE"


@dataclass
class Mutant:
    """A single AST-level mutation."""

    category: MutationCategory
    original_node: ast.AST
    mutated_node: ast.AST
    description: str
    location: int = 0  # line number in original source


@dataclass
class MutantResult:
    """Result of evaluating a single mutant against tests."""

    mutant: Mutant
    killed: bool = False
    killed_by: str | None = None  # "assertion" | "crash" | "timeout" | None
    test_name: str | None = None
    elapsed_ms: float = 0.0


@dataclass
class CategoryResult:
    """Aggregated results for one mutation category."""

    category: MutationCategory
    total: int = 0
    killed: int = 0
    survived: int = 0
    killed_by_assertion: int = 0
    killed_by_crash: int = 0
    timed_out: int = 0

    @property
    def survival_rate(self) -> float:
        return self.survived / self.total if self.total > 0 else 0.0


@dataclass
class SamplingResult:
    """Result of inline mutation sampling for a function."""

    function_key: str = ""
    categories_tested: int = 0
    total_mutants: int = 0
    total_killed: int = 0
    total_survived: int = 0
    survival_rate: float = 0.0
    coverage_depth: str = "sampled"
    per_category: list[CategoryResult] = field(default_factory=list)
    budget_exhausted: bool = False
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "function_key": self.function_key,
            "categories_tested": self.categories_tested,
            "total_mutants": self.total_mutants,
            "total_killed": self.total_killed,
            "total_survived": self.total_survived,
            "survival_rate": round(self.survival_rate, 3),
            "coverage_depth": self.coverage_depth,
            "budget_exhausted": self.budget_exhausted,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "per_category": [
                {
                    "category": cr.category.value,
                    "total": cr.total,
                    "killed": cr.killed,
                    "survived": cr.survived,
                    "survival_rate": round(cr.survival_rate, 3),
                }
                for cr in self.per_category
            ],
        }


@dataclass
class ProfilingResult:
    """Result of exhaustive mutation profiling for a function."""

    function_key: str = ""
    categories_tested: int = 0
    total_mutants: int = 0
    total_killed: int = 0
    total_survived: int = 0
    survival_rate: float = 0.0
    coverage_depth: str = "profiled"
    is_gateable: bool = True
    per_category: list[CategoryResult] = field(default_factory=list)
    kill_matrix: dict[str, list[str]] = field(default_factory=dict)
    """Maps mutant description → list of test names that killed it."""
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "function_key": self.function_key,
            "categories_tested": self.categories_tested,
            "total_mutants": self.total_mutants,
            "total_killed": self.total_killed,
            "total_survived": self.total_survived,
            "survival_rate": round(self.survival_rate, 3),
            "coverage_depth": self.coverage_depth,
            "is_gateable": self.is_gateable,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "per_category": [
                {
                    "category": cr.category.value,
                    "total": cr.total,
                    "killed": cr.killed,
                    "survived": cr.survived,
                    "killed_by_assertion": cr.killed_by_assertion,
                    "killed_by_crash": cr.killed_by_crash,
                    "survival_rate": round(cr.survival_rate, 3),
                }
                for cr in self.per_category
            ],
        }

"""Decomposition heuristic detection based on mutation survival rates."""

from dataclasses import dataclass

from lintgate.mutation.state import MutationStateManager


@dataclass
class DecompositionCandidate:
    function_id: str
    file_path: str
    survival_rate: float
    surviving_categories: list[str]
    total_mutants: int
    reason: str


class DecompositionDetector:
    """Heuristic detector for functions requiring structural decomposition."""

    DECOMPOSITION_THRESHOLD = 0.50
    MIN_CATEGORIES = 3

    def __init__(self, state_manager: MutationStateManager):
        self.state_manager = state_manager

    def get_candidates(self, file_path: str = None) -> list[DecompositionCandidate]:
        """Scan all mutation state to find decomposition candidates."""
        candidates = []
        for state in self.state_manager.state.values():
            if file_path and not state.file_path.endswith(file_path):
                continue

            if state.total == 0:
                continue

            rate = state.survival_rate
            surviving_cats = [c for c, count in state.survived_by_category.items() if count > 0]

            if rate >= self.DECOMPOSITION_THRESHOLD and len(surviving_cats) >= self.MIN_CATEGORIES:
                candidates.append(DecompositionCandidate(
                    function_id=f"{state.file_path}::{state.function_name}",
                    file_path=state.file_path,
                    survival_rate=rate,
                    surviving_categories=surviving_cats,
                    total_mutants=state.total,
                    reason=f"High survival ({rate:.0%}) across {len(surviving_cats)} semantic categories indicates high structural entanglement.",
                ))

        return sorted(candidates, key=lambda c: c.survival_rate, reverse=True)

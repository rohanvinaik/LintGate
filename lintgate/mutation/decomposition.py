"""Decomposition heuristic detection based on mutation survival rates."""

from dataclasses import dataclass, field
from typing import Any

from lintgate.mutation.state import MutationStateManager, SurvivorSite


@dataclass
class DecompositionCandidate:
    function_id: str
    file_path: str
    survival_rate: float
    surviving_categories: list[str]
    total_mutants: int
    reason: str


@dataclass
class DecompositionAxis:
    """A decomposition axis representing a cluster of survivor sites.

    Clusters are formed by grouping survivor sites that are:
    1. In contiguous or near-contiguous line ranges (within gap tolerance)
    2. Dominated by a single category (>=70% of sites in the cluster)
    """

    category: str
    line_start: int
    line_end: int
    site_count: int
    dominant_ratio: float

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {
            "category": self.category,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "site_count": self.site_count,
            "dominant_ratio": self.dominant_ratio,
        }


@dataclass
class DecompositionPlan:
    """A decomposition plan for a function with survivor sites.

    Contains deterministic axes that represent suggested decomposition
    boundaries based on spatial clustering of survivor sites.
    """

    function_id: str
    file_path: str
    axes: list[DecompositionAxis] = field(default_factory=list)
    survivor_site_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {
            "function_id": self.function_id,
            "file_path": self.file_path,
            "axes": [a.to_dict() for a in self.axes],
            "survivor_site_count": self.survivor_site_count,
        }


class DecompositionDetector:
    """Heuristic detector for functions requiring structural decomposition."""

    DECOMPOSITION_THRESHOLD = 0.50
    MIN_CATEGORIES = 3
    CLUSTER_GAP_TOLERANCE = 5  # Max gap between lines to be considered contiguous
    CATEGORY_DOMINANCE_THRESHOLD = 0.70  # 70% same category to emit axis

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
                candidates.append(
                    DecompositionCandidate(
                        function_id=f"{state.file_path}::{state.function_name}",
                        file_path=state.file_path,
                        survival_rate=rate,
                        surviving_categories=surviving_cats,
                        total_mutants=state.total,
                        reason=f"High survival ({rate:.0%}) across {len(surviving_cats)} semantic categories indicates high structural entanglement.",
                    )
                )

        return sorted(candidates, key=lambda c: c.survival_rate, reverse=True)

    def create_decomposition_plan(
        self,
        function_id: str,
        survivor_sites: list[SurvivorSite],
    ) -> DecompositionPlan | None:
        """Create a decomposition plan by clustering survivor sites.

        Args:
            function_id: The function identifier (e.g., "file.py::func")
            survivor_sites: List of SurvivorSite objects for the function

        Returns:
            DecompositionPlan with clustered axes, or None if no valid plan
        """
        # Handle empty or invalid data - return no-plan result
        if not survivor_sites:
            return None

        # Filter out sites with invalid line numbers (sentinel values)
        valid_sites = [s for s in survivor_sites if s.line > 0]
        if not valid_sites:
            return None

        # Sort by line for deterministic clustering
        sorted_sites = sorted(valid_sites, key=lambda s: s.line)

        # Create clusters
        clusters = self._create_clusters(sorted_sites)

        # Filter clusters by category dominance and create axes
        axes = []
        for cluster in clusters:
            # Check category dominance
            category_counts: dict[str, int] = {}
            for site in cluster:
                category_counts[site.category] = category_counts.get(site.category, 0) + 1

            if not category_counts:
                continue

            max_category = max(category_counts.items(), key=lambda x: x[1])
            dominant_ratio = max_category[1] / len(cluster)

            # Only include axes that meet dominance threshold
            if dominant_ratio >= self.CATEGORY_DOMINANCE_THRESHOLD:
                line_start = min(s.line for s in cluster)
                line_end = max(s.line for s in cluster)

                axis = DecompositionAxis(
                    category=max_category[0],
                    line_start=line_start,
                    line_end=line_end,
                    site_count=len(cluster),
                    dominant_ratio=dominant_ratio,
                )
                axes.append(axis)

        # Deterministic sort: by line_start, then category
        axes = sorted(axes, key=lambda a: (a.line_start, a.category))

        # Extract file_path from function_id
        file_path = ""
        if "::" in function_id:
            file_path = function_id.split("::")[0]

        return DecompositionPlan(
            function_id=function_id,
            file_path=file_path,
            axes=axes,
            survivor_site_count=len(valid_sites),
        )

    def _create_clusters(self, sorted_sites: list[SurvivorSite]) -> list[list[SurvivorSite]]:
        """Create clusters from sorted survivor sites based on line proximity.

        Sites within CLUSTER_GAP_TOLERANCE lines are clustered together.
        """
        if not sorted_sites:
            return []

        clusters: list[list[SurvivorSite]] = []
        current_cluster: list[SurvivorSite] = [sorted_sites[0]]

        for i in range(1, len(sorted_sites)):
            prev_site = sorted_sites[i - 1]
            curr_site = sorted_sites[i]

            # Check if current site is within gap tolerance of previous
            gap = curr_site.line - prev_site.line
            if gap <= self.CLUSTER_GAP_TOLERANCE:
                current_cluster.append(curr_site)
            else:
                # Start new cluster
                clusters.append(current_cluster)
                current_cluster = [curr_site]

        # Don't forget the last cluster
        if current_cluster:
            clusters.append(current_cluster)

        return clusters

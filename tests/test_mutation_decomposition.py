"""Tests for decomposition planning and survivor site clustering."""

from lintgate.mutation.decomposition import (
    DecompositionAxis,
    DecompositionDetector,
    DecompositionPlan,
)
from lintgate.mutation.state import MutationStateManager, SurvivorSite


class TestDecompositionAxis:
    """Tests for DecompositionAxis dataclass."""

    def test_to_dict(self):
        """Test serialization of DecompositionAxis."""
        axis = DecompositionAxis(
            category="arithmetic",
            line_start=10,
            line_end=20,
            site_count=5,
            dominant_ratio=0.8,
        )
        d = axis.to_dict()
        assert d["category"] == "arithmetic"
        assert d["line_start"] == 10
        assert d["line_end"] == 20
        assert d["site_count"] == 5
        assert d["dominant_ratio"] == 0.8


class TestDecompositionPlan:
    """Tests for DecompositionPlan dataclass."""

    def test_to_dict(self):
        """Test serialization of DecompositionPlan."""
        axis = DecompositionAxis(
            category="arithmetic",
            line_start=10,
            line_end=20,
            site_count=5,
            dominant_ratio=0.8,
        )
        plan = DecompositionPlan(
            function_id="test.py::func",
            file_path="test.py",
            axes=[axis],
            survivor_site_count=5,
        )
        d = plan.to_dict()
        assert d["function_id"] == "test.py::func"
        assert d["file_path"] == "test.py"
        assert len(d["axes"]) == 1
        assert d["survivor_site_count"] == 5


class TestDecompositionDetectorClustering:
    """Tests for survivor site clustering in DecompositionDetector."""

    def test_create_decomposition_plan_empty_sites(self, tmp_path):
        """Test that empty survivor sites returns None."""
        storage = tmp_path / "state.json"
        manager = MutationStateManager(storage)
        detector = DecompositionDetector(manager)

        result = detector.create_decomposition_plan("test.py::func", [])
        assert result is None

    def test_create_decomposition_plan_no_survivor_sites(self, tmp_path):
        """Test that None survivor sites returns None."""
        storage = tmp_path / "state.json"
        manager = MutationStateManager(storage)
        detector = DecompositionDetector(manager)

        result = detector.create_decomposition_plan("test.py::func", None)
        assert result is None

    def test_create_decomposition_plan_invalid_line_numbers(self, tmp_path):
        """Test that sentinel line numbers (-1) are filtered out."""
        storage = tmp_path / "state.json"
        manager = MutationStateManager(storage)
        detector = DecompositionDetector(manager)

        sites = [
            SurvivorSite(line=-1, column=0, category="unknown", mutant_id="mut_1"),
            SurvivorSite(line=-1, column=0, category="unknown", mutant_id="mut_2"),
        ]
        result = detector.create_decomposition_plan("test.py::func", sites)
        assert result is None

    def test_create_decomposition_plan_single_site(self, tmp_path):
        """Test single survivor site creates valid plan."""
        storage = tmp_path / "state.json"
        manager = MutationStateManager(storage)
        detector = DecompositionDetector(manager)

        sites = [
            SurvivorSite(line=10, column=5, category="arithmetic", mutant_id="mut_1"),
        ]
        result = detector.create_decomposition_plan("test.py::func", sites)

        assert result is not None
        assert result.function_id == "test.py::func"
        assert result.survivor_site_count == 1

    def test_create_decomposition_plan_clusters_by_proximity(self, tmp_path):
        """Test that sites within gap tolerance are clustered together."""
        storage = tmp_path / "state.json"
        manager = MutationStateManager(storage)
        detector = DecompositionDetector(manager)

        # Sites within CLUSTER_GAP_TOLERANCE (5 lines) should be in one cluster
        sites = [
            SurvivorSite(line=10, column=5, category="arithmetic", mutant_id="mut_1"),
            SurvivorSite(line=12, column=5, category="arithmetic", mutant_id="mut_2"),
            SurvivorSite(line=14, column=5, category="arithmetic", mutant_id="mut_3"),
            # This one exceeds gap tolerance - new cluster
            SurvivorSite(line=25, column=5, category="conditional", mutant_id="mut_4"),
        ]
        result = detector.create_decomposition_plan("test.py::func", sites)

        assert result is not None
        assert result.survivor_site_count == 4
        # First cluster should have dominant ratio (3/3 = 1.0 >= 0.7)
        # Second cluster should have 1/1 = 1.0 >= 0.7
        assert len(result.axes) == 2

    def test_create_decomposition_plan_filters_non_dominant(self, tmp_path):
        """Test that clusters below dominance threshold are excluded."""
        storage = tmp_path / "state.json"
        manager = MutationStateManager(storage)
        detector = DecompositionDetector(manager)

        # Mixed category cluster - not 70% dominant
        sites = [
            SurvivorSite(line=10, column=5, category="arithmetic", mutant_id="mut_1"),
            SurvivorSite(line=12, column=5, category="conditional", mutant_id="mut_2"),
            SurvivorSite(line=14, column=5, category="string", mutant_id="mut_3"),
        ]
        result = detector.create_decomposition_plan("test.py::func", sites)

        # No axis because no cluster has >= 70% dominance
        assert result is not None
        assert len(result.axes) == 0

    def test_deterministic_sort_order(self, tmp_path):
        """Test that repeated runs produce identical axes (deterministic)."""
        storage = tmp_path / "state.json"
        manager = MutationStateManager(storage)
        detector = DecompositionDetector(manager)

        sites = [
            SurvivorSite(line=30, column=5, category="arithmetic", mutant_id="mut_1"),
            SurvivorSite(line=10, column=5, category="arithmetic", mutant_id="mut_2"),
            SurvivorSite(line=20, column=5, category="conditional", mutant_id="mut_3"),
        ]
        result1 = detector.create_decomposition_plan("test.py::func", sites)

        # Run again to verify determinism
        sites2 = [
            SurvivorSite(line=30, column=5, category="arithmetic", mutant_id="mut_1"),
            SurvivorSite(line=10, column=5, category="arithmetic", mutant_id="mut_2"),
            SurvivorSite(line=20, column=5, category="conditional", mutant_id="mut_3"),
        ]
        result2 = detector.create_decomposition_plan("test.py::func", sites2)

        assert result1 is not None
        assert result2 is not None

        # Verify same axes in same order
        assert len(result1.axes) == len(result2.axes)
        for a1, a2 in zip(result1.axes, result2.axes, strict=True):
            assert a1.line_start == a2.line_start
            assert a1.category == a2.category


class TestDecompositionDetector:
    """Tests for existing DecompositionDetector functionality."""

    def test_get_candidates(self, tmp_path):
        """Test that get_candidates still works."""
        storage = tmp_path / "state.json"
        manager = MutationStateManager(storage)
        detector = DecompositionDetector(manager)

        # Empty state should return no candidates
        candidates = detector.get_candidates()
        assert len(candidates) == 0

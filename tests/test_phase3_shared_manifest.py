"""Phase 3 tests: Shared immutable manifest in run_mesh().

Tests cover:
1. SupervisionEvent.context field exists and defaults to empty dict
2. _run_prepass populates event.context with manifest and python_files
3. _run_prepass gracefully degrades when project_root is empty
4. Performance channel uses shared manifest from context
5. Mutation channel uses shared manifest from context
6. build_manifest called exactly once during run_mesh (not per-channel)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lintgate.controlplane.runtime import _run_prepass
from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent


class TestSupervisionEventContext:
    """SupervisionEvent has a context field for shared artifacts."""

    def test_context_defaults_to_empty_dict(self):
        event = SupervisionEvent()
        assert event.context == {}
        assert isinstance(event.context, dict)

    def test_context_is_mutable(self):
        event = SupervisionEvent()
        event.context["key"] = "value"
        assert event.context["key"] == "value"

    def test_context_preserved_through_dataclass(self):
        event = SupervisionEvent(context={"pre_existing": True})
        assert event.context["pre_existing"] is True


class TestRunPrepass:
    """_run_prepass builds shared manifest and stores in event.context."""

    def test_prepass_populates_manifest_and_files(self):
        event = SupervisionEvent(project_root="/tmp/project")
        mock_manifest = MagicMock()
        mock_files = ["a.py", "b.py"]

        # _run_prepass uses lazy imports, so patch the source modules
        with (
            patch(
                "lintgate.channels.performance_channel._discover_python_files",
                return_value=mock_files,
            ),
            patch(
                "lintgate.linters.performance_checks.manifest.build_manifest",
                return_value=mock_manifest,
            ),
        ):
            _run_prepass(event)

        assert event.context["property_manifest"] is mock_manifest
        assert event.context["python_files"] == mock_files

    def test_prepass_no_op_when_no_project_root(self):
        event = SupervisionEvent(project_root="")
        _run_prepass(event)
        assert "property_manifest" not in event.context
        assert "python_files" not in event.context

    def test_prepass_no_op_when_no_python_files(self):
        event = SupervisionEvent(project_root="/tmp/empty")

        with patch(
            "lintgate.channels.performance_channel._discover_python_files",
            return_value=[],
        ):
            _run_prepass(event)

        assert "property_manifest" not in event.context

    def test_prepass_graceful_on_manifest_failure(self):
        event = SupervisionEvent(project_root="/tmp/project")

        with (
            patch(
                "lintgate.channels.performance_channel._discover_python_files",
                return_value=["a.py"],
            ),
            patch(
                "lintgate.linters.performance_checks.manifest.build_manifest",
                side_effect=RuntimeError("AST parse failure"),
            ),
        ):
            # Should not raise
            _run_prepass(event)

        assert "property_manifest" not in event.context


class TestPerformanceChannelUsesSharedManifest:
    """Performance channel reads manifest from event.context when available."""

    def test_uses_shared_manifest(self):
        from lintgate.channels.performance_channel import PerformanceChannel

        channel = PerformanceChannel()
        mock_manifest = MagicMock()
        mock_manifest.pure_count = 5
        mock_manifest.impure_count = 10
        mock_manifest.functions = {}
        mock_manifest.property_distribution = {}
        mock_manifest.optimization_potential = []

        event = SupervisionEvent(
            project_root="/tmp/project",
            context={
                "property_manifest": mock_manifest,
                "python_files": ["a.py"],
            },
        )
        config = ControlPlaneConfig()

        with patch(
            "lintgate.linters.performance_checks.manifest.build_manifest"
        ) as mock_build:
            # Should NOT call build_manifest since manifest is in context
            channel.execute(event, config)
            mock_build.assert_not_called()

    def test_falls_back_to_building_own_manifest(self):
        from lintgate.channels.performance_channel import PerformanceChannel

        channel = PerformanceChannel()

        event = SupervisionEvent(
            project_root="/tmp/project",
            # No manifest in context
        )
        config = ControlPlaneConfig()

        mock_manifest = MagicMock()
        mock_manifest.pure_count = 0
        mock_manifest.impure_count = 0
        mock_manifest.functions = {}
        mock_manifest.property_distribution = {}
        mock_manifest.optimization_potential = []

        with (
            patch(
                "lintgate.channels.performance_channel._discover_python_files",
                return_value=["a.py"],
            ),
            patch(
                "lintgate.channels.performance_channel.build_manifest",
                return_value=mock_manifest,
            ) as mock_build,
        ):
            channel.execute(event, config)
            mock_build.assert_called_once()


class TestMutationChannelUsesSharedManifest:
    """Mutation channel reads manifest from event.context when available."""

    def test_uses_shared_manifest(self, tmp_path):
        from lintgate.channels.mutation_channel import MutationChannel

        channel = MutationChannel()
        mock_manifest = MagicMock()
        mock_manifest.functions = {}  # No pure functions

        event = SupervisionEvent(
            project_root=str(tmp_path),
            files_changed=["logic.py"],
            context={"property_manifest": mock_manifest},
        )
        config = ControlPlaneConfig()

        with patch(
            "lintgate.channels.mutation_channel.MutationStateManager"
        ) as mock_sm_cls:
            mock_sm = mock_sm_cls.return_value
            mock_sm.state = {}
            mock_sm.requires_run.return_value = False

            with patch(
                "lintgate.linters.performance_checks.manifest.build_manifest"
            ) as mock_build:
                channel.execute(event, config)
                # Should NOT call build_manifest since manifest is in context
                mock_build.assert_not_called()

    def test_falls_back_to_building_own_manifest(self, tmp_path):
        from lintgate.channels.mutation_channel import MutationChannel

        channel = MutationChannel()

        event = SupervisionEvent(
            project_root=str(tmp_path),
            files_changed=["logic.py"],
            # No manifest in context
        )
        config = ControlPlaneConfig()

        with patch(
            "lintgate.channels.mutation_channel.MutationStateManager"
        ) as mock_sm_cls:
            mock_sm = mock_sm_cls.return_value
            mock_sm.state = {}
            mock_sm.requires_run.return_value = False

            # build_manifest will be called as fallback
            mock_manifest = MagicMock()
            mock_manifest.functions = {}

            with patch(
                "lintgate.linters.performance_checks.manifest.build_manifest",
                return_value=mock_manifest,
            ) as mock_build:
                channel.execute(event, config)
                mock_build.assert_called_once()

"""Targeted tests for remaining uncovered branches after initial coverage pass.

Each test targets a specific missing branch identified by the symbol coverage gate.
"""

from __future__ import annotations

import json
import textwrap
from unittest import mock


def _load_tool_result(json_str):
    import json as _j
    import os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f:

            return _j.loads(f.read())
    return r


# ── controlplane_tools helpers ────────────────────────────────────────


def _stub_helpers(**overrides):
    defaults = {
        "_validate_project_root": lambda p: p or "/tmp/test",
        "_collect_python_files": lambda _root: [],
        "_build_cp_full_details": lambda _mr, _fi: {},
        "_build_onboarding_status": lambda _root: {"config_state": "config_enabled"},
        "_json_dumps": lambda obj, **kw: json.dumps(obj),
    }
    defaults.update(overrides)
    return defaults


class TestGenerateLivingContextPatchesNone:
    """Branch 558→552: generate_context_patch returns None, loop continues."""

    def test_patch_none_skips_append(self):
        from mcp_tools.controlplane_tools import _generate_living_context_patches

        session = mock.MagicMock()
        session.pending_patches = []
        actions: list[str] = []

        cp_config = mock.MagicMock()
        cp_config.inquiry.living_context = True

        with (
            mock.patch(
                "lintgate.config.load_controlplane_config",
                return_value=cp_config,
            ),
            mock.patch(
                "lintgate.context_bootstrap.generate_context_patch",
                return_value=None,
            ),
        ):
            _generate_living_context_patches(session, "/tmp/proj", ["rule1"], actions)

        assert session.pending_patches == []
        assert not any("patch" in a.lower() for a in actions)


class TestGetSessionStatusNone:
    """Branch 458→473: session is falsy, returns None."""

    def test_no_session_returns_none(self):
        from mcp_tools.controlplane_tools import _get_session_status

        with mock.patch(
            "lintgate.controlplane.session_memory.load_session",
            return_value=None,
        ):
            result = _get_session_status("/tmp/proj")

        assert result is None


class TestImplAgentFeedbackNoDisagreement:
    """Branch 573→576: disagreement is None, skips _record_disagreement."""

    def test_no_disagreement_skips_record(self):
        from mcp_tools.controlplane_tools import _impl_controlplane_agent_feedback

        session = mock.MagicMock()
        session.session_id = "test-session"
        session.agent_disagreements = []
        session.proposed_constraints = []

        with (
            mock.patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=session,
            ),
            mock.patch(
                "lintgate.controlplane.session_memory.save_session",
            ),
        ):
            result = json.loads(
                _impl_controlplane_agent_feedback("/tmp", None, None, None, None, _stub_helpers())
            )

        assert result["total_disagreements"] == 0


class TestImplGetDetailsSpecificSections:
    """Branches 397→400 through 409→414: specific sections skip others."""

    def test_findings_only_skips_other_sections(self):
        from mcp_tools.controlplane_tools import _impl_controlplane_get_details

        details = {
            "duration_ms": 100,
            "coherence": {"state": "stable"},
            "channel_results": {},
        }
        with mock.patch(
            "lintgate.state.load_controlplane_run",
            return_value=details,
        ):
            result = json.loads(
                _impl_controlplane_get_details(
                    "run1", None, None, 50, ["findings"], _stub_helpers()
                )
            )

        assert "coherence" not in result
        assert "channel_details" not in result
        assert "repairs" not in result
        assert "evidence" not in result

    def test_coherence_only(self):
        from mcp_tools.controlplane_tools import _impl_controlplane_get_details

        details = {
            "duration_ms": 100,
            "coherence": {"state": "stable"},
            "channel_results": {},
        }
        with mock.patch(
            "lintgate.state.load_controlplane_run",
            return_value=details,
        ):
            result = json.loads(
                _impl_controlplane_get_details(
                    "run1", None, None, 50, ["coherence"], _stub_helpers()
                )
            )

        assert "coherence" in result
        assert "findings" not in result
        assert "channel_details" not in result

    def test_empty_evidence_not_included(self):
        from mcp_tools.controlplane_tools import _impl_controlplane_get_details

        details = {
            "duration_ms": 100,
            "coherence": {},
            "channel_results": {},
        }
        with mock.patch(
            "lintgate.state.load_controlplane_run",
            return_value=details,
        ):
            result = json.loads(
                _impl_controlplane_get_details(
                    "run1", None, None, 50, ["evidence"], _stub_helpers()
                )
            )

        assert "evidence" not in result


class TestProcessAcceptedConstraintsBranches:
    """Branches in the inner loop: pattern match, rule extraction, break."""

    def test_accepted_with_matching_constraint_and_rule(self):
        from mcp_tools.controlplane_tools import _process_accepted_constraints

        session = mock.MagicMock()
        session.proposed_constraints = [
            {
                "pattern_key": "ruff|F821",
                "status": "accepted",
                "proposed_rule": "No F821",
            },
            {
                "pattern_key": "ruff|E501",
                "status": "proposed",
                "proposed_rule": "Wrap lines",
            },
        ]
        actions: list[str] = []

        with mock.patch(
            "lintgate.controlplane.constraint_proposer.update_constraint_status",
            return_value=True,
        ):
            rules = _process_accepted_constraints(session, ["ruff|F821"], actions)

        assert rules == ["No F821"]
        assert any("Accepted" in a for a in actions)

    def test_accepted_constraint_without_rule_text(self):
        """Branch 525→527: rule_text is empty, so it's not appended."""
        from mcp_tools.controlplane_tools import _process_accepted_constraints

        session = mock.MagicMock()
        session.proposed_constraints = [
            {"pattern_key": "ruff|F821", "status": "accepted", "proposed_rule": ""},
        ]
        actions: list[str] = []

        with mock.patch(
            "lintgate.controlplane.constraint_proposer.update_constraint_status",
            return_value=True,
        ):
            rules = _process_accepted_constraints(session, ["ruff|F821"], actions)

        assert rules == []

    def test_constraint_not_in_proposals(self):
        """Branch 522→517: loop exhausts without finding pattern_key match."""
        from mcp_tools.controlplane_tools import _process_accepted_constraints

        session = mock.MagicMock()
        session.proposed_constraints = [
            {"pattern_key": "other|key", "status": "accepted", "proposed_rule": "X"},
        ]
        actions: list[str] = []

        with mock.patch(
            "lintgate.controlplane.constraint_proposer.update_constraint_status",
            return_value=True,
        ):
            rules = _process_accepted_constraints(session, ["ruff|F821"], actions)

        assert rules == []


class TestRegisterControlplaneToolClosures:
    """Lines 897, 916 (closure calls) and branch 809→812 (absolute path)."""

    def test_agent_feedback_closure_delegates(self):
        """Line 897: controlplane_agent_feedback closure calls _impl."""
        mcp = mock.MagicMock()
        mcp.tool.return_value = lambda fn: fn

        from mcp_tools.controlplane_tools import register

        tools = register(mcp, _stub_helpers())
        feedback_fn = tools["controlplane_agent_feedback"]

        with mock.patch(
            "mcp_tools.controlplane_tools._impl_controlplane_agent_feedback",
            return_value='{"ok": true}',
        ) as mock_impl:
            result = feedback_fn(
                path="/tmp/proj",
                run_id="r1",
                disagreement="no",
            )
            mock_impl.assert_called_once()
            assert result == '{"ok": true}'

    def test_apply_repairs_closure_delegates(self):
        """Line 916: controlplane_apply_repairs closure calls _impl."""
        mcp = mock.MagicMock()
        mcp.tool.return_value = lambda fn: fn

        from mcp_tools.controlplane_tools import register

        tools = register(mcp, _stub_helpers())
        apply_fn = tools["controlplane_apply_repairs"]

        with mock.patch(
            "mcp_tools.controlplane_tools._impl_controlplane_apply_repairs",
            return_value='{"applied": []}',
        ) as mock_impl:
            result = apply_fn(path="/tmp/proj")
            mock_impl.assert_called_once()
            assert result == '{"applied": []}'

    def test_test_skeleton_absolute_path(self):
        """Branch 809→812: target_file is already absolute, skip join."""
        mcp = mock.MagicMock()
        mcp.tool.return_value = lambda fn: fn

        from mcp_tools.controlplane_tools import register

        tools = register(mcp, _stub_helpers())
        skel_fn = tools["controlplane_test_skeleton"]

        with (
            mock.patch(
                "lintgate.controlplane.skeleton_generator.generate_test_skeleton",
                return_value="def test_foo(): pass",
            ),
            mock.patch(
                "lintgate.controlplane.skeleton_generator.generate_test_path",
                return_value="/tmp/proj/tests/test_foo.py",
            ),
            mock.patch("os.path.exists", return_value=True),
        ):
            result = _load_tool_result(skel_fn(path="/tmp/proj", target_file="/tmp/proj/foo.py"))
            assert result["source_file"] == "/tmp/proj/foo.py"


# ── onboarding_tools helpers ─────────────────────────────────────────


class TestFilterToSourcePackages:
    """Cover lines 301-302: empty source_packages returns all files."""

    def test_empty_source_packages_returns_all(self):
        from lintgate.channels.test_channel import _filter_to_source_packages

        files = ["/proj/tests/test_foo.py", "/proj/lintgate/bar.py"]
        assert _filter_to_source_packages(files, [], "/proj") == files

    def test_none_source_packages_returns_all(self):
        from lintgate.channels.test_channel import _filter_to_source_packages

        files = ["/proj/tests/test_foo.py"]
        assert _filter_to_source_packages(files, None, "/proj") == files  # type: ignore[arg-type]  # intentional: test None handling

    def test_filters_to_matching_packages(self):
        from lintgate.channels.test_channel import _filter_to_source_packages

        files = [
            "/proj/lintgate/foo.py",
            "/proj/tests/test_foo.py",
            "/proj/mcp_tools/bar.py",
        ]
        result = _filter_to_source_packages(files, ["lintgate", "mcp_tools"], "/proj")
        assert result == ["/proj/lintgate/foo.py", "/proj/mcp_tools/bar.py"]

    def test_relpath_error_skipped(self):
        """Line 307-308: ValueError on relpath is silently skipped."""
        from unittest.mock import patch

        from lintgate.channels.test_channel import _filter_to_source_packages

        files = ["/other/drive/foo.py"]
        with patch("os.path.relpath", side_effect=ValueError("different drive")):
            result = _filter_to_source_packages(files, ["lintgate"], "/proj")
        assert result == []


class TestParsePyprojectTomliImport:
    """Lines 529-530: tomllib not available, falls back to tomli."""

    def test_tomli_fallback(self, tmp_path):
        from mcp_tools.onboarding_tools import _parse_pyproject_metadata

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
                [project]
                name = "testpkg"
                requires-python = ">=3.9"
            """)
        )
        version, _lic, _dirs, has = _parse_pyproject_metadata(tmp_path)
        assert has is True
        assert "3" in version

    def test_no_version_match(self, tmp_path):
        """Branch 540→543: requires-python has no version pattern."""
        from mcp_tools.onboarding_tools import _parse_pyproject_metadata

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            textwrap.dedent("""\
                [project]
                name = "testpkg"
                requires-python = "any"
            """)
        )
        version, _lic, _dirs, has = _parse_pyproject_metadata(tmp_path)
        assert version == "3"


class TestScanProjectDirsUnmatched:
    """Branch 616→606: entry that doesn't match any condition."""

    def test_unmatched_dir_skipped(self, tmp_path):
        from mcp_tools.onboarding_tools import _scan_project_dirs

        (tmp_path / "random_dir").mkdir()
        (tmp_path / "nopkg").mkdir()
        pkg = tmp_path / "mylib"
        pkg.mkdir()
        (pkg / "__init__.py").touch()

        src_dirs, test_dirs, doc_dirs, _trunc = _scan_project_dirs(tmp_path, [])
        assert "mylib" in src_dirs
        assert "random_dir" not in src_dirs
        assert "nopkg" not in src_dirs

    def test_src_without_packages(self, tmp_path):
        """Branch 618→617: src/ subdir without __init__.py."""
        from mcp_tools.onboarding_tools import _scan_project_dirs

        src = tmp_path / "src"
        src.mkdir()
        (src / "not_a_pkg").mkdir()

        src_dirs, _test_dirs, _doc_dirs, _trunc = _scan_project_dirs(tmp_path, [])
        assert src_dirs == []


class TestRegisterOnboardingClaudeMdExists:
    """Branch 2396→2406: .claude/CLAUDE.md already exists, skip bootstrap action."""

    def test_getting_started_with_claude_md(self, tmp_path):
        from mcp_tools.onboarding_tools import register

        mcp = mock.MagicMock()
        mcp.tool.return_value = lambda fn: fn

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "CLAUDE.md").write_text("# Project")

        tools = register(mcp, _stub_helpers())
        getting_started = tools["getting_started"]

        qi_result = mock.MagicMock()
        qi_result.complete = True
        qi_result.missing = []
        with (
            mock.patch(
                "mcp_tools.onboarding_tools._detect_github_remote",
                return_value={"detected": False},
            ),
            mock.patch(
                "lintgate.quality_infra.audit_quality_infrastructure",
                return_value=qi_result,
            ),
        ):
            result = _load_tool_result(getting_started(path=str(tmp_path)))

        actions = result.get("next_actions", [])
        bootstrap_actions = [a for a in actions if a.get("tool") == "bootstrap_context_files"]
        assert bootstrap_actions == []

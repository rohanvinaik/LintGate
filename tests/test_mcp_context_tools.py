"""Coverage tests for mcp_tools/context_tools.py.

Exercises the register() function and each MCP tool it registers:
context_guidance, audit_context_health, bootstrap_context_files,
context_patch_review, context_patch_apply, extract_theory_constraints,
extract_project_theory, build_theory_pack, get_theory_context.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from mcp_tools.context_tools import register


def _load_tool_result(json_str):
    import os
    r = json.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and os.path.isfile(r.get("file","")):
        with open(r["file"]) as f:

            return json.loads(f.read())
    return r


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_helpers(tmp_path: Path) -> dict:
    """Build a minimal helpers dict that validates against tmp_path."""
    return {
        "_validate_project_root": lambda path, **kw: str(tmp_path),
    }


def _register_tools(tmp_path: Path) -> dict:
    """Register tools on a mock MCP and return the tool function dict."""
    mcp = MagicMock()
    # Make @mcp.tool() a no-op decorator: mcp.tool() returns identity.
    mcp.tool.return_value = lambda fn: fn
    helpers = _make_helpers(tmp_path)
    return register(mcp, helpers)  # type: ignore[no-any-return]


# ── register() ───────────────────────────────────────────────────────────


class TestRegister:
    def test_register_returns_all_tool_names(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        expected = {
            "context_guidance",
            "audit_context_health",
            "bootstrap_context_files",
            "context_patch_review",
            "context_patch_apply",
            "extract_theory_constraints",
            "extract_project_theory",
            "build_theory_pack",
            "get_theory_context",
        }
        assert set(tools.keys()) == expected

    def test_register_values_are_callable(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        for name, fn in tools.items():
            assert callable(fn), f"{name} is not callable"


# ── context_guidance ─────────────────────────────────────────────────────


class TestContextGuidance:
    def test_returns_valid_json(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_guidance = {"directives": {"critical": []}, "rules": []}
        with (
            patch(
                "lintgate.context_guidance.build_context_guidance",
                return_value=mock_guidance,
            ),
            patch(
                "lintgate.context_guidance.summarize_context_guidance",
                return_value="All good",
            ),
        ):
            result = _load_tool_result(tools["context_guidance"](path=str(tmp_path)))
        assert result["summary"] == "All good"

    def test_with_files_argument(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_guidance = {"directives": {"critical": []}, "rules": []}
        with (
            patch(
                "lintgate.context_guidance.build_context_guidance",
                return_value=mock_guidance,
            ) as mock_build,
            patch(
                "lintgate.context_guidance.summarize_context_guidance",
                return_value="Summary",
            ),
        ):
            result = _load_tool_result(tools["context_guidance"](path=str(tmp_path), files=["foo.py"]))
        mock_build.assert_called_once_with(str(tmp_path), files=["foo.py"])
        assert isinstance(result, dict)


# ── audit_context_health ─────────────────────────────────────────────────


class TestAuditContextHealth:
    def test_returns_valid_json(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_audit = {"score": 80, "findings": [], "status": "healthy"}
        with patch(
            "lintgate.context_auditor.audit_context_health",
            return_value=mock_audit,
        ):
            result = _load_tool_result(tools["audit_context_health"](path=str(tmp_path)))
        assert result["score"] == 80


# ── bootstrap_context_files ──────────────────────────────────────────────


class TestBootstrapContextFiles:
    def test_returns_valid_json_defaults(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_result = {"claude_md": "# Project", "agents_md": "# Agents"}
        with (
            patch(
                "lintgate.context_bootstrap.bootstrap_context_files",
                return_value=mock_result,
            ),
            patch("lintgate.state.log_feature_usage"),
        ):
            result = _load_tool_result(tools["bootstrap_context_files"](path=str(tmp_path)))
        assert "claude_md" in result

    def test_write_mode_passes_through(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_result = {"written": True}
        with (
            patch(
                "lintgate.context_bootstrap.bootstrap_context_files",
                return_value=mock_result,
            ) as mock_bs,
            patch("lintgate.state.log_feature_usage"),
        ):
            tools["bootstrap_context_files"](path=str(tmp_path), write=True)
        mock_bs.assert_called_once()
        call_kwargs = mock_bs.call_args
        assert call_kwargs[1]["write"] is True

    def test_telemetry_failure_suppressed(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_result = {"ok": True}
        with (
            patch(
                "lintgate.context_bootstrap.bootstrap_context_files",
                return_value=mock_result,
            ),
            patch(
                "lintgate.state.log_feature_usage",
                side_effect=RuntimeError("telemetry broken"),
            ),
        ):
            # Should not raise despite telemetry failure
            result = _load_tool_result(tools["bootstrap_context_files"](path=str(tmp_path)))
        assert result["ok"] is True

    def test_model_id_passes_through(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_result = {"ok": True}
        with (
            patch(
                "lintgate.context_bootstrap.bootstrap_context_files",
                return_value=mock_result,
            ) as mock_bs,
            patch("lintgate.state.log_feature_usage"),
        ):
            tools["bootstrap_context_files"](path=str(tmp_path), model_id="anthropic:claude-opus-4")
        assert mock_bs.call_args[1]["model_id"] == "anthropic:claude-opus-4"


# ── context_patch_review ─────────────────────────────────────────────────


class TestContextPatchReview:
    def test_no_pending_patches(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_session = MagicMock()
        mock_session.pending_patches = []
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value=mock_session,
        ):
            result = _load_tool_result(tools["context_patch_review"](path=str(tmp_path)))
        assert result["pending_count"] == 0
        assert "No pending" in result["message"]

    def test_with_pending_patches(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_session = MagicMock()
        mock_session.pending_patches = [
            {
                "status": "pending",
                "patch_id": "p1",
                "section_id": "machine_rules",
                "trigger": "constraint_accepted",
                "old_content": "",
                "new_content": "rule1",
                "rationale": "test",
                "evidence": {"key": "val"},
                "created_at": 0.0,
            }
        ]
        mock_refreshed_patch = MagicMock()
        mock_refreshed_patch.patch_id = "p1"
        mock_refreshed_patch.section_id = "machine_rules"
        mock_refreshed_patch.trigger = "constraint_accepted"
        mock_refreshed_patch.rationale = "test"

        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=mock_session,
            ),
            patch(
                "lintgate.context_bootstrap.generate_context_patch",
                return_value=mock_refreshed_patch,
            ),
            patch(
                "lintgate.context_bootstrap.apply_context_patch",
                return_value={"diff_preview": "--- old\n+++ new"},
            ),
        ):
            result = _load_tool_result(tools["context_patch_review"](path=str(tmp_path)))
        assert result["pending_count"] == 1
        assert len(result["patches"]) == 1
        assert result["patches"][0]["status"] == "pending"

    def test_patch_refresh_returns_none(self, tmp_path: Path) -> None:
        """When generate_context_patch returns None, patch is marked no_op."""
        tools = _register_tools(tmp_path)
        mock_session = MagicMock()
        mock_session.pending_patches = [
            {
                "status": "pending",
                "patch_id": "p2",
                "section_id": "do_dont",
                "trigger": "test",
                "old_content": "",
                "new_content": "",
                "rationale": "test",
                "evidence": {},
                "created_at": 0.0,
            }
        ]
        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=mock_session,
            ),
            patch(
                "lintgate.context_bootstrap.generate_context_patch",
                return_value=None,
            ),
        ):
            result = _load_tool_result(tools["context_patch_review"](path=str(tmp_path)))
        assert result["patches"][0]["status"] == "no_op"
        assert result["patches"][0]["diff_preview"] is None


# ── context_patch_apply ──────────────────────────────────────────────────


class TestContextPatchApply:
    def test_no_matching_patches(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_session = MagicMock()
        mock_session.pending_patches = []
        with patch(
            "lintgate.controlplane.session_memory.get_or_create_session",
            return_value=mock_session,
        ):
            result = _load_tool_result(tools["context_patch_apply"](path=str(tmp_path)))
        assert result["applied"] == 0
        assert "No matching" in result["message"]

    def test_apply_all_pending(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_session = MagicMock()
        patch_dict = {
            "status": "pending",
            "patch_id": "p1",
            "section_id": "machine_rules",
            "trigger": "constraint_accepted",
            "old_content": "",
            "new_content": "rule",
            "rationale": "test",
            "evidence": {},
            "created_at": 0.0,
        }
        mock_session.pending_patches = [patch_dict]

        mock_refreshed = MagicMock()
        mock_refreshed.patch_id = "p1"
        mock_refreshed.section_id = "machine_rules"

        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=mock_session,
            ),
            patch(
                "lintgate.controlplane.session_memory.save_session",
            ),
            patch(
                "lintgate.context_bootstrap.generate_context_patch",
                return_value=mock_refreshed,
            ),
            patch(
                "lintgate.context_bootstrap.apply_context_patch",
                return_value={"applied": True, "diff_preview": "+rule"},
            ),
            patch("lintgate.state.log_feature_usage"),
        ):
            result = _load_tool_result(tools["context_patch_apply"](path=str(tmp_path)))
        assert result["applied"] == 1
        assert result["dry_run"] is False

    def test_apply_with_patch_ids_filter(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_session = MagicMock()
        p1 = {
            "status": "pending",
            "patch_id": "p1",
            "section_id": "s1",
            "trigger": "t",
            "old_content": "",
            "new_content": "x",
            "rationale": "r",
            "evidence": {},
            "created_at": 0.0,
        }
        p2 = {
            "status": "pending",
            "patch_id": "p2",
            "section_id": "s2",
            "trigger": "t",
            "old_content": "",
            "new_content": "y",
            "rationale": "r",
            "evidence": {},
            "created_at": 0.0,
        }
        mock_session.pending_patches = [p1, p2]

        mock_refreshed = MagicMock()
        mock_refreshed.patch_id = "p2"
        mock_refreshed.section_id = "s2"

        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=mock_session,
            ),
            patch(
                "lintgate.controlplane.session_memory.save_session",
            ),
            patch(
                "lintgate.context_bootstrap.generate_context_patch",
                return_value=mock_refreshed,
            ),
            patch(
                "lintgate.context_bootstrap.apply_context_patch",
                return_value={"applied": True, "diff_preview": "+y"},
            ),
            patch("lintgate.state.log_feature_usage"),
        ):
            result = _load_tool_result(tools["context_patch_apply"](path=str(tmp_path), patch_ids=["p2"]))
        # Only p2 should be processed
        assert result["applied"] == 1
        assert len(result["results"]) == 1

    def test_dry_run_does_not_save(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_session = MagicMock()
        patch_dict = {
            "status": "pending",
            "patch_id": "p1",
            "section_id": "s1",
            "trigger": "t",
            "old_content": "",
            "new_content": "x",
            "rationale": "r",
            "evidence": {},
            "created_at": 0.0,
        }
        mock_session.pending_patches = [patch_dict]

        mock_refreshed = MagicMock()
        mock_refreshed.patch_id = "p1"
        mock_refreshed.section_id = "s1"

        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=mock_session,
            ),
            patch(
                "lintgate.controlplane.session_memory.save_session",
            ) as mock_save,
            patch(
                "lintgate.context_bootstrap.generate_context_patch",
                return_value=mock_refreshed,
            ),
            patch(
                "lintgate.context_bootstrap.apply_context_patch",
                return_value={"applied": False, "diff_preview": "+x"},
            ),
        ):
            result = _load_tool_result(tools["context_patch_apply"](path=str(tmp_path), dry_run=True))
        assert result["dry_run"] is True
        mock_save.assert_not_called()

    def test_apply_noop_patch_marks_applied(self, tmp_path: Path) -> None:
        """When generate_context_patch returns None, patch is marked applied."""
        tools = _register_tools(tmp_path)
        mock_session = MagicMock()
        patch_dict = {
            "status": "pending",
            "patch_id": "p1",
            "section_id": "s1",
            "trigger": "t",
            "old_content": "",
            "new_content": "x",
            "rationale": "r",
            "evidence": {},
            "created_at": 0.0,
        }
        mock_session.pending_patches = [patch_dict]

        with (
            patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=mock_session,
            ),
            patch(
                "lintgate.controlplane.session_memory.save_session",
            ),
            patch(
                "lintgate.context_bootstrap.generate_context_patch",
                return_value=None,
            ),
            patch("lintgate.state.log_feature_usage"),
        ):
            result = _load_tool_result(tools["context_patch_apply"](path=str(tmp_path)))
        assert result["results"][0]["status"] == "no_op"
        assert result["results"][0]["applied"] is False


# ── extract_theory_constraints ───────────────────────────────────────────


class TestExtractTheoryConstraints:
    def test_returns_enforceable_rules(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_result = {
            "enforceable_rules": [
                {"type": "LINTGATE_FORBID_REGEX", "pattern": "eval\\("},
            ],
            "profile": {},
        }
        with patch(
            "lintgate.theory_extractor.extract_theory",
            return_value=mock_result,
        ):
            result = _load_tool_result(tools["extract_theory_constraints"](path=str(tmp_path)))
        assert isinstance(result, list)
        assert result[0]["type"] == "LINTGATE_FORBID_REGEX"


# ── extract_project_theory ───────────────────────────────────────────────


class TestExtractProjectTheory:
    def test_returns_full_theory(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_result = {
            "profile": {"core_theory": []},
            "enforceable_rules": [],
        }
        with (
            patch(
                "lintgate.theory_extractor.extract_theory",
                return_value=mock_result,
            ),
            patch("lintgate.state.log_feature_usage"),
        ):
            result = _load_tool_result(tools["extract_project_theory"](path=str(tmp_path)))
        assert "profile" in result

    def test_telemetry_logged(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_result: dict[str, Any] = {"profile": {}, "enforceable_rules": []}
        with (
            patch(
                "lintgate.theory_extractor.extract_theory",
                return_value=mock_result,
            ),
            patch("lintgate.state.log_feature_usage") as mock_log,
        ):
            tools["extract_project_theory"](path=str(tmp_path))
        mock_log.assert_called_once_with("theory_extraction", str(tmp_path))


# ── build_theory_pack ────────────────────────────────────────────────────


class TestBuildTheoryPack:
    def test_returns_packed_summary(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_pack = {"summary": "Project uses X", "rules": []}
        with (
            patch(
                "lintgate.theory_extractor.build_theory_pack",
                return_value=mock_pack,
            ),
            patch("lintgate.state.log_feature_usage"),
        ):
            result = _load_tool_result(tools["build_theory_pack"](path=str(tmp_path)))
        assert result["summary"] == "Project uses X"

    def test_include_full_profile_passed(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_pack = {"summary": "X", "full_profile": {"a": 1}}
        with (
            patch(
                "lintgate.theory_extractor.build_theory_pack",
                return_value=mock_pack,
            ) as mock_build,
            patch("lintgate.state.log_feature_usage"),
        ):
            tools["build_theory_pack"](path=str(tmp_path), include_full_profile=True)
        assert mock_build.call_args[1]["include_full_profile"] is True


# ── get_theory_context ───────────────────────────────────────────────────


class TestGetTheoryContext:
    def test_returns_claims(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_ctx = {"claims": [{"text": "Prefer deterministic checks"}]}
        with patch(
            "lintgate.theory_extractor.get_theory_context",
            return_value=mock_ctx,
        ):
            result = _load_tool_result(tools["get_theory_context"](path=str(tmp_path)))
        assert "claims" in result

    def test_max_claims_zero_raises(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        with pytest.raises(ValueError, match="max_claims must be > 0"):
            tools["get_theory_context"](path=str(tmp_path), max_claims=0)

    def test_negative_max_claims_raises(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        with pytest.raises(ValueError, match="max_claims must be > 0"):
            tools["get_theory_context"](path=str(tmp_path), max_claims=-1)

    def test_facet_and_keywords_pass_through(self, tmp_path: Path) -> None:
        tools = _register_tools(tmp_path)
        mock_ctx: dict[str, list[str]] = {"claims": []}
        with patch(
            "lintgate.theory_extractor.get_theory_context",
            return_value=mock_ctx,
        ) as mock_get:
            tools["get_theory_context"](
                path=str(tmp_path),
                facet="core_theory",
                keywords=["deterministic"],
                max_claims=3,
            )
        mock_get.assert_called_once_with(
            str(tmp_path),
            facet="core_theory",
            keywords=["deterministic"],
            max_claims=3,
        )

"""Tests for prescriptive MCP impl orchestration layer."""

from __future__ import annotations

import json

from lintgate.specification.prescriptive.spec import (
    Invariant,
    PrescriptiveSpec,
    pred_gt,
    save_spec,
)

def _load_tool_result(json_str):
    import json as _j, os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f: return _j.loads(f.read())
    return r



def _make_spec(target_key="mod::func", **overrides):
    defaults = {
        "spec_id": "t1",
        "target_key": target_key,
        "problem_class": "pure",
        "mode": "prospective",
        "invariants": [
            Invariant("inv1", pred_gt("result", 0), "positive", "src", 0.8, "safety"),
        ],
        "prescriptive_sigma": 5,
        "created_at": 1000.0,
    }
    defaults.update(overrides)
    return PrescriptiveSpec(**defaults)


class TestImplCompose:
    def test_compose_prospective(self, tmp_path):
        from mcp_tools._prescriptive_impl import impl_prescriptive_spec_compose

        # Create minimal project structure
        (tmp_path / ".claude").mkdir()
        helpers = {"_validate_project_root": lambda _: str(tmp_path)}

        result = impl_prescriptive_spec_compose(
            str(tmp_path),
            "mod::func",
            "prospective",
            helpers,
        )
        data = json.loads(result) if isinstance(result, str) else result
        assert data.get("target_key") == "mod::func" or "spec_id" in data
        assert "next_actions" in data

    def test_compose_auto_mode(self, tmp_path):
        from mcp_tools._prescriptive_impl import impl_prescriptive_spec_compose

        helpers = {"_validate_project_root": lambda _: str(tmp_path)}

        result = impl_prescriptive_spec_compose(
            str(tmp_path),
            "mod::func",
            "auto",
            helpers,
        )
        data = json.loads(result) if isinstance(result, str) else result
        assert "mode" in data or "spec_id" in data


class TestImplCompile:
    def test_compile_missing_spec(self, tmp_path):
        from mcp_tools._prescriptive_impl import impl_prescriptive_spec_compile

        helpers = {"_validate_project_root": lambda _: str(tmp_path)}

        result = impl_prescriptive_spec_compile(str(tmp_path), "nonexistent::func", helpers)
        data = json.loads(result) if isinstance(result, str) else result
        assert "error" in data

    def test_compile_existing_spec(self, tmp_path):
        from mcp_tools._prescriptive_impl import impl_prescriptive_spec_compile

        spec = _make_spec()
        save_spec(str(tmp_path), spec)
        helpers = {"_validate_project_root": lambda _: str(tmp_path)}

        result = impl_prescriptive_spec_compile(str(tmp_path), "mod::func", helpers)
        data = json.loads(result) if isinstance(result, str) else result
        assert "spec_id" in data or "property_tests" in data


class TestImplVerify:
    def test_verify_no_specs(self, tmp_path):
        from mcp_tools._prescriptive_impl import impl_prescriptive_spec_verify

        helpers = {"_validate_project_root": lambda _: str(tmp_path)}

        result = impl_prescriptive_spec_verify(str(tmp_path), "mod.py", None, helpers)
        data = json.loads(result) if isinstance(result, str) else result
        assert data.get("status") in ("no_specs", "no_spec") or "error" not in data

    def test_verify_with_spec(self, tmp_path):
        from mcp_tools._prescriptive_impl import impl_prescriptive_spec_verify

        spec = _make_spec()
        save_spec(str(tmp_path), spec)
        # Create the source file
        (tmp_path / "mod.py").write_text("def func():\n    return 1\n")
        helpers = {"_validate_project_root": lambda _: str(tmp_path)}

        result = impl_prescriptive_spec_verify(str(tmp_path), "mod.py", "func", helpers)
        data = json.loads(result) if isinstance(result, str) else result
        assert "overall" in data or "status" in data


class TestImplStatus:
    def test_status_empty(self, tmp_path):
        from mcp_tools._prescriptive_impl import impl_prescriptive_spec_status

        helpers = {"_validate_project_root": lambda _: str(tmp_path)}

        result = impl_prescriptive_spec_status(str(tmp_path), helpers)
        data = json.loads(result) if isinstance(result, str) else result
        assert data.get("total_specs") == 0

    def test_status_with_specs(self, tmp_path):
        from mcp_tools._prescriptive_impl import impl_prescriptive_spec_status

        save_spec(str(tmp_path), _make_spec("mod::a"))
        save_spec(str(tmp_path), _make_spec("mod::b", spec_id="t2"))
        helpers = {"_validate_project_root": lambda _: str(tmp_path)}

        result = impl_prescriptive_spec_status(str(tmp_path), helpers)
        data = json.loads(result) if isinstance(result, str) else result
        assert data["total_specs"] == 2
        assert "specs" in data


class TestLoadTheoryProfile:
    def test_cached_file(self, tmp_path):
        from mcp_tools._prescriptive_impl import _load_theory_profile

        lintgate_dir = tmp_path / ".lintgate"
        lintgate_dir.mkdir()
        (lintgate_dir / "theory_profile.json").write_text(
            json.dumps({"core_theory": {"claims": [{"text": "test", "confidence": 0.9}]}})
        )
        result = _load_theory_profile(str(tmp_path))
        assert "core_theory" in result

    def test_missing_file(self, tmp_path):
        from mcp_tools._prescriptive_impl import _load_theory_profile

        result = _load_theory_profile(str(tmp_path))
        # Either empty dict or extracted from project
        assert isinstance(result, dict)


class TestRenderGenerationPrompt:
    def test_renders_constraints(self):
        from mcp_tools._prescriptive_impl import _render_generation_prompt

        constraints = [
            {"constraint_type": "must_not_use", "description": "No mutation", "priority": 1},
            {"constraint_type": "must_use", "description": "Return int", "priority": 3},
        ]
        result = _render_generation_prompt("mod::func", constraints)
        assert "MUST NOT" in result
        assert "MUST:" in result
        assert "mod::func" in result

    def test_empty_constraints(self):
        from mcp_tools._prescriptive_impl import _render_generation_prompt

        result = _render_generation_prompt("mod::func", [])
        assert "Generation Constraints" in result


class TestTargetHelpers:
    def test_target_to_file(self):
        from mcp_tools._prescriptive_impl import _target_to_file

        assert _target_to_file("mod/core::func") == "mod/core.py"
        assert _target_to_file("plain") == "plain"

    def test_target_to_func(self):
        from mcp_tools._prescriptive_impl import _target_to_func

        assert _target_to_func("mod::func") == "func"
        assert _target_to_func("plain") is None

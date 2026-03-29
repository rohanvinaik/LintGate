"""Tests for mcp_tools/_mutation_tools_impl.py — sub-module level coverage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from mcp_tools._mutation_tools_impl import (
    _build_mutation_context,
    _collect_prescriptions,
    impl_clear_state,
    impl_decompose,
    impl_get_state,
    impl_prescribe,
    impl_prescribe_tests,
    impl_run_full,
    impl_run_sampling,
)


def _load_tool_result(json_str):
    import json as _j
    import os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f: return _j.loads(f.read())
    return r


# ── helpers ────────────────────────────────────────────────────────


def _make_helpers(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "_validate_project_root": lambda p: "/test/project",
        "_json_dumps": lambda d, **kw: json.dumps(d),
        "_json_loads": lambda s: json.loads(s),
    }
    base.update(overrides)
    return base


# ── _build_mutation_context ────────────────────────────────────────


@patch("mcp_tools._mutation_tools_impl.discover_test_files", return_value=["tests/test_foo.py"])
@patch("mcp_tools._mutation_tools_impl.detect_purity_map", return_value={"func_a": True})
@patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=Path("/tmp/cache"))
def test_build_mutation_context(mock_cache, mock_purity, mock_tests):
    ctx = _build_mutation_context("/test/project", "/test/project/src/foo.py")
    assert ctx.full_path == "/test/project/src/foo.py"
    assert ctx.rel_path == "src/foo.py"
    assert ctx.cache_dir == Path("/tmp/cache")
    assert ctx.purity_map == {"func_a": True}
    assert ctx.test_files == ["tests/test_foo.py"]


@patch("mcp_tools._mutation_tools_impl.discover_test_files", return_value=[])
@patch("mcp_tools._mutation_tools_impl.detect_purity_map", return_value={})
@patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=Path("/tmp/cache"))
def test_build_mutation_context_no_tests(mock_cache, mock_purity, mock_tests):
    ctx = _build_mutation_context("/test/project", "/test/project/lib.py")
    assert ctx.test_files == []
    assert ctx.purity_map == {}


@patch(
    "mcp_tools._mutation_tools_impl.discover_test_files",
    return_value=["/tmp/proj/tests/test_foo.py"],
)
@patch("mcp_tools._mutation_tools_impl.detect_purity_map", return_value={})
@patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=Path("/tmp/cache"))
def test_build_mutation_context_merges_generated_tests(
    mock_cache, mock_purity, mock_tests, tmp_path
):
    project_root = str(tmp_path)
    source_file = tmp_path / "src" / "foo.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("def foo():\n    return 1\n")

    generated = tmp_path / "tests" / "generated" / "test_src_foo.py"
    generated.parent.mkdir(parents=True)
    generated.write_text("def test_generated():\n    assert True\n")

    ctx = _build_mutation_context(
        project_root,
        str(source_file),
        extra_test_files=["tests/generated/test_src_foo.py", "tests/generated/missing.py"],
    )
    assert ctx.test_files == [
        "/tmp/proj/tests/test_foo.py",
        str(generated),
    ]


# ── impl_run_sampling ──────────────────────────────────────────────


@patch("mcp_tools._mutation_tools_impl.resolve_function")
def test_run_sampling_function_not_found(mock_resolve):
    mock_resolve.return_value = ("/test/project/foo.py", None, "Function 'bar' not found")
    helpers = _make_helpers()
    result_raw = impl_run_sampling(helpers, "/test", "foo.py", "bar", 1000.0)
    result = _load_tool_result(result_raw)
    assert result["error"] == "Function 'bar' not found"


@patch("mcp_tools._mutation_tools_impl._build_mutation_context")
@patch("mcp_tools._mutation_tools_impl.run_on_functions_with_tests", return_value="error string")
@patch("mcp_tools._mutation_tools_impl.resolve_function")
def test_run_sampling_returns_error_string(mock_resolve, mock_run, mock_ctx):
    mock_resolve.return_value = ("/test/project/foo.py", None, None)
    mock_ctx.return_value = MagicMock(test_files=["t.py"])
    helpers = _make_helpers()
    result = impl_run_sampling(helpers, "/test", "foo.py", None, 1000.0)
    assert result == "error string"


@patch("mcp_tools._mutation_tools_impl._build_mutation_context")
@patch("mcp_tools._mutation_tools_impl.run_on_functions_with_tests")
@patch("mcp_tools._mutation_tools_impl.resolve_function")
def test_run_sampling_success(mock_resolve, mock_run, mock_ctx):
    mock_resolve.return_value = ("/test/project/foo.py", None, None)
    mock_ctx_obj = MagicMock(test_files=["t1.py", "t2.py"])
    mock_ctx.return_value = mock_ctx_obj
    mock_run.return_value = [{"func": "add", "survived": 0}]
    helpers = _make_helpers()
    result_raw = impl_run_sampling(helpers, "/test", "foo.py", None, 1000.0)
    result = _load_tool_result(result_raw)
    assert result["functions_sampled"] == 1
    assert result["tests_discovered"] == 2
    assert "next_actions" in result


# ── impl_run_full ──────────────────────────────────────────────────


@patch("mcp_tools._mutation_tools_impl.resolve_function")
def test_run_full_function_not_found(mock_resolve):
    mock_resolve.return_value = ("/test/project/foo.py", None, "Function 'baz' not found")
    helpers = _make_helpers()
    result_raw = impl_run_full(helpers, "/test", "foo.py", "baz")
    result = _load_tool_result(result_raw)
    assert result["error"] == "Function 'baz' not found"


@patch("mcp_tools._mutation_tools_impl.run_post_profiling_analysis", return_value={"ok": True})
@patch("mcp_tools._mutation_tools_impl._build_mutation_context")
@patch("mcp_tools._mutation_tools_impl.run_on_functions_with_tests")
@patch("mcp_tools._mutation_tools_impl.resolve_function")
def test_run_full_success(mock_resolve, mock_run, mock_ctx, mock_analysis):
    mock_resolve.return_value = ("/test/project/foo.py", None, None)
    mock_ctx_obj = MagicMock(test_files=["t.py"], purity_map={})
    mock_ctx.return_value = mock_ctx_obj
    mock_run.return_value = [{"func": "add"}]
    helpers = _make_helpers()
    result_raw = impl_run_full(helpers, "/test", "foo.py", None)
    result = _load_tool_result(result_raw)
    assert result["functions_profiled"] == 1
    assert result["analysis"] == {"ok": True}
    assert "next_actions" in result


def test_run_full_bare_method_uses_qualified_owner_and_kills_mutants(tmp_path):
    src = tmp_path / "widget.py"
    src.write_text(
        "\n".join(
            [
                "class Widget:",
                "    def __init__(self, count=1):",
                "        self.count = count",
                "",
                "    def to_dict(self):",
                "        return {'count': self.count}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    test_file = tmp_path / "tests" / "test_widget.py"
    test_file.parent.mkdir()
    test_file.write_text(
        "\n".join(
            [
                "from widget import Widget",
                "",
                "class TestWidgetMutationKillers:",
                "    def test_to_dict_exact(self):",
                "        assert Widget(count=2).to_dict() == {'count': 2}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    helpers = _make_helpers(
        _validate_project_root=lambda _path: str(tmp_path),
        _json_dumps=lambda d, **kw: json.dumps(d),
    )

    result_raw = impl_run_full(helpers, str(tmp_path), "widget.py", "to_dict")
    result = _load_tool_result(result_raw)
    entry = result["results"][0]

    assert entry["function_key"] == "widget.py::Widget.to_dict"
    assert entry["tests_loaded"] >= 1
    assert entry["total_killed"] >= 1
    assert entry["survival_rate"] < 1.0


# ── impl_get_state ─────────────────────────────────────────────────


@patch("mcp_tools._mutation_tools_impl.get_cache_dir")
def test_get_state_no_cache_dir(mock_cache):
    mock_cache.return_value = Path("/nonexistent/cache/dir")
    helpers = _make_helpers()
    result_raw = impl_get_state(helpers, "/test", None, None)
    result = _load_tool_result(result_raw)
    assert result["note"] == "No mutation data yet"
    assert "next_actions" in result


@patch("mcp_tools._mutation_tools_impl.iter_cached_states")
@patch("mcp_tools._mutation_tools_impl.get_cache_dir")
def test_get_state_with_data(mock_cache, mock_iter):
    cache_dir = Path("/tmp/existing_cache")
    mock_cache.return_value = cache_dir
    with patch.object(Path, "exists", return_value=True):
        mock_iter.return_value = [
            {"function_key": "mod.py::func_a", "survival_rate": 0.5},
            {"function_key": "mod.py::func_b", "survival_rate": 0.0},
        ]
        helpers = _make_helpers()
        result_raw = impl_get_state(helpers, "/test", "mod.py", None)
        result = _load_tool_result(result_raw)
        assert result["total_functions"] == 2
        assert len(result["states"]) == 2


@patch("mcp_tools._mutation_tools_impl.iter_cached_states")
@patch("mcp_tools._mutation_tools_impl.get_cache_dir")
def test_get_state_empty_cache(mock_cache, mock_iter):
    cache_dir = Path("/tmp/empty_cache")
    mock_cache.return_value = cache_dir
    with patch.object(Path, "exists", return_value=True):
        mock_iter.return_value = []
        helpers = _make_helpers()
        result_raw = impl_get_state(helpers, "/test", None, None)
        result = _load_tool_result(result_raw)
        assert result["total_functions"] == 0
        assert result["states"] == []


# ── _collect_prescriptions ─────────────────────────────────────────


def test_collect_prescriptions_empty_states():
    result = _collect_prescriptions([])
    assert result == []


@patch(
    "mcp_tools._mutation_tools_impl.prescription_for_category",
    return_value="Add exact-value assertions",
)
def test_collect_prescriptions_with_survivors(mock_presc):
    states = [
        {
            "function_key": "mod.py::func_a",
            "per_category": [
                {"category": "VALUE", "survived": 2, "survival_rate": 0.5},
                {"category": "SWAP", "survived": 0, "survival_rate": 0.0},
            ],
        },
    ]
    result = _collect_prescriptions(states)
    assert len(result) == 1
    assert result[0]["function"] == "mod.py::func_a"
    assert result[0]["category"] == "VALUE"
    assert result[0]["survived"] == 2
    assert result[0]["survival_rate"] == 0.5
    assert result[0]["action"] == "Add exact-value assertions"


@patch("mcp_tools._mutation_tools_impl.prescription_for_category", return_value="action")
def test_collect_prescriptions_multiple_survivors(mock_presc):
    states = [
        {
            "function_key": "a.py::f1",
            "per_category": [
                {"category": "VALUE", "survived": 1, "survival_rate": 0.25},
                {"category": "BOUNDARY", "survived": 3, "survival_rate": 0.75},
            ],
        },
        {
            "function_key": "b.py::f2",
            "per_category": [
                {"category": "SWAP", "survived": 1, "survival_rate": 0.5},
            ],
        },
    ]
    result = _collect_prescriptions(states)
    assert len(result) == 3


def test_collect_prescriptions_no_survivors():
    states = [
        {
            "function_key": "mod.py::func",
            "per_category": [
                {"category": "VALUE", "survived": 0, "survival_rate": 0.0},
            ],
        },
    ]
    result = _collect_prescriptions(states)
    assert result == []


# ── impl_prescribe ─────────────────────────────────────────────────


@patch("mcp_tools._mutation_tools_impl.iter_cached_states", return_value=[])
@patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=Path("/tmp/c"))
def test_prescribe_no_data(mock_cache, mock_iter):
    helpers = _make_helpers()
    result_raw = impl_prescribe(helpers, "/test", None, None)
    result = _load_tool_result(result_raw)
    assert result["note"] == "No mutation data — run sampling first"


@patch("mcp_tools._mutation_tools_impl.prescription_for_category", return_value="fix it")
@patch("mcp_tools._mutation_tools_impl.iter_cached_states")
@patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=Path("/tmp/c"))
def test_prescribe_with_data(mock_cache, mock_iter, mock_presc):
    mock_iter.return_value = [
        {
            "function_key": "x.py::fn",
            "per_category": [{"category": "VALUE", "survived": 1, "survival_rate": 0.5}],
        },
    ]
    helpers = _make_helpers()
    result_raw = impl_prescribe(helpers, "/test", "x.py", None)
    result = _load_tool_result(result_raw)
    assert result["total_prescriptions"] == 1
    assert result["prescriptions"][0]["category"] == "VALUE"
    assert "next_actions" in result


# ── impl_decompose ─────────────────────────────────────────────────


@patch("mcp_tools._mutation_tools_impl.iter_cached_states", return_value=[])
@patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=Path("/tmp/c"))
def test_decompose_no_candidates(mock_cache, mock_iter):
    helpers = _make_helpers()
    result_raw = impl_decompose(helpers, "/test", "foo.py", None, "suggest")
    result = _load_tool_result(result_raw)
    assert result["mode"] == "suggest"
    assert result["candidates"] == []


@patch("mcp_tools._mutation_tools_impl.iter_cached_states")
@patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=Path("/tmp/c"))
def test_decompose_with_candidates(mock_cache, mock_iter):
    mock_iter.return_value = [
        {
            "function_key": "mod.py::complex_fn",
            "per_category": [
                {"category": "VALUE", "survived": 2},
                {"category": "SWAP", "survived": 1},
                {"category": "BOUNDARY", "survived": 0},
            ],
        },
    ]
    helpers = _make_helpers()
    result_raw = impl_decompose(helpers, "/test", "mod.py", None, "suggest")
    result = _load_tool_result(result_raw)
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["function"] == "mod.py::complex_fn"
    assert "VALUE" in result["candidates"][0]["surviving_categories"]
    assert "SWAP" in result["candidates"][0]["surviving_categories"]
    assert "BOUNDARY" not in result["candidates"][0]["surviving_categories"]


@patch("mcp_tools._mutation_tools_impl.iter_cached_states")
@patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=Path("/tmp/c"))
def test_decompose_single_survivor_not_candidate(mock_cache, mock_iter):
    mock_iter.return_value = [
        {
            "function_key": "mod.py::fn",
            "per_category": [
                {"category": "VALUE", "survived": 1},
                {"category": "SWAP", "survived": 0},
            ],
        },
    ]
    helpers = _make_helpers()
    result_raw = impl_decompose(helpers, "/test", "mod.py", None, "suggest")
    result = _load_tool_result(result_raw)
    assert result["candidates"] == []


# ── impl_prescribe_tests ──────────────────────────────────────────


@patch("mcp_tools._mutation_tools_impl.iter_cached_states", return_value=[])
@patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=Path("/tmp/c"))
def test_prescribe_tests_no_data(mock_cache, mock_iter):
    helpers = _make_helpers()
    result_raw = impl_prescribe_tests(helpers, "/test", "foo.py", None)
    result = _load_tool_result(result_raw)
    assert result["skeletons"] == []


@patch("mcp_tools._mutation_tools_impl.generate_test_skeleton")
@patch("mcp_tools._mutation_tools_impl.iter_cached_states")
@patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=Path("/tmp/c"))
def test_prescribe_tests_with_survivors(mock_cache, mock_iter, mock_skel):
    mock_iter.return_value = [
        {
            "function_key": "m.py::fn",
            "per_category": [
                {"category": "VALUE", "survived": 1},
                {"category": "SWAP", "survived": 0},
            ],
        },
    ]
    mock_skel.return_value = {
        "function": "m.py::fn",
        "category": "VALUE",
        "test_name": "test_fn_value",
        "skeleton": "def test_fn_value(): ...",
    }
    helpers = _make_helpers()
    result_raw = impl_prescribe_tests(helpers, "/test", "m.py", None)
    result = _load_tool_result(result_raw)
    assert len(result["skeletons"]) == 1
    assert result["skeletons"][0]["category"] == "VALUE"
    assert "next_actions" in result


@patch("mcp_tools._mutation_tools_impl._load_golden_captures")
@patch("mcp_tools._mutation_tools_impl._resolve_func_node", return_value=None)
@patch("mcp_tools._mutation_tools_impl.iter_cached_states")
@patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=Path("/tmp/c"))
def test_prescribe_tests_fills_value_from_golden(mock_cache, mock_iter, mock_resolve, mock_golden):
    del mock_cache, mock_resolve
    mock_iter.return_value = [
        {
            "function_key": "pkg/mod.py::fn",
            "survivor_records": [
                {
                    "category": "VALUE",
                    "mutant_id": "VALUE_0",
                }
            ],
            "call_site_inputs": [],
            "per_category": [],
        }
    ]
    mock_golden.return_value = {
        "pkg/mod.py::fn": {
            "inputs": [1],
            "kwargs": {"flag": True},
            "output": "42",
            "provenance": "corroborated",
            "corroborating_lens": "pure_deterministic",
        }
    }

    helpers = _make_helpers()
    result_raw = impl_prescribe_tests(helpers, "/test", "pkg/mod.py", None)
    result = _load_tool_result(result_raw)
    assert len(result["skeletons"]) == 1
    skeleton = result["skeletons"][0]
    assert skeleton["source"] == "golden_capture"
    assert skeleton["needs_oracle"] is False
    assert "repr(result)" in skeleton["test_code"]
    assert "fn(1, flag=True)" in skeleton["test_code"]
    assert "== '42'" in skeleton["test_code"]


@patch("mcp_tools._mutation_tools_impl._load_golden_captures")
@patch("mcp_tools._mutation_tools_impl._resolve_func_node", return_value=None)
@patch("mcp_tools._mutation_tools_impl.iter_cached_states")
@patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=Path("/tmp/c"))
def test_prescribe_tests_golden_capture_flag(mock_cache, mock_iter, mock_resolve, mock_golden):
    """Verify golden_capture_used is True when golden capture is used, False otherwise."""
    del mock_cache, mock_resolve
    mock_iter.return_value = [
        {
            "function_key": "pkg/mod.py::fn",
            "survivor_records": [
                {"category": "VALUE", "mutant_id": "VALUE_0"},
                {"category": "SWAP", "mutant_id": "SWAP_0"},
            ],
            "call_site_inputs": [],
            "per_category": [],
        }
    ]
    mock_golden.return_value = {
        "pkg/mod.py::fn": {
            "inputs": [1],
            "kwargs": {},
            "output": "42",
            "provenance": "corroborated",
            "corroborating_lens": "pure_deterministic",
        }
    }

    helpers = _make_helpers()
    result_raw = impl_prescribe_tests(helpers, "/test", "pkg/mod.py", None)
    result = _load_tool_result(result_raw)
    by_cat = {s["category"]: s for s in result["skeletons"]}
    # VALUE skeleton should have golden_capture_used=True
    assert by_cat["VALUE"]["golden_capture_used"] is True
    # SWAP skeleton should have golden_capture_used=False
    assert by_cat["SWAP"]["golden_capture_used"] is False


@patch("mcp_tools._mutation_tools_impl.generate_test_skeleton")
@patch("mcp_tools._mutation_tools_impl.iter_cached_states")
@patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=Path("/tmp/c"))
def test_prescribe_tests_generic_skeleton_golden_flag(mock_cache, mock_iter, mock_skel):
    """Verify golden_capture_used=False on generic (non-survivor) skeletons."""
    mock_iter.return_value = [
        {
            "function_key": "m.py::fn",
            "per_category": [{"category": "BOUNDARY", "survived": 1}],
        },
    ]
    mock_skel.return_value = {
        "function": "m.py::fn",
        "category": "BOUNDARY",
        "test_name": "test_fn_boundary",
        "skeleton": "def test_fn_boundary(): ...",
    }
    helpers = _make_helpers()
    result_raw = impl_prescribe_tests(helpers, "/test", "m.py", None)
    result = _load_tool_result(result_raw)
    assert len(result["skeletons"]) == 1
    assert result["skeletons"][0]["golden_capture_used"] is False


# ── impl_clear_state ───────────────────────────────────────────────


@patch("mcp_tools._mutation_tools_impl.get_cache_dir")
def test_clear_state_no_cache(mock_cache):
    mock_cache.return_value = Path("/nonexistent/path")
    helpers = _make_helpers()
    result_raw = impl_clear_state(helpers, "/test", None)
    result = _load_tool_result(result_raw)
    assert result["note"] == "No mutation state to clear"


def test_clear_state_clears_files(tmp_path):
    cache_dir = tmp_path / "mutation"
    cache_dir.mkdir()
    (cache_dir / "func_a.json").write_text('{"function_key": "mod.py::func_a"}')
    (cache_dir / "func_b.json").write_text('{"function_key": "mod.py::func_b"}')
    (cache_dir / "scheduler_state.json").write_text("{}")

    with patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=cache_dir):
        helpers = _make_helpers()
        result_raw = impl_clear_state(helpers, "/test", None)
        result = _load_tool_result(result_raw)
    assert result["cleared"] == 2
    assert (cache_dir / "scheduler_state.json").exists() is True


def test_clear_state_filters_by_file(tmp_path):
    cache_dir = tmp_path / "mutation"
    cache_dir.mkdir()
    (cache_dir / "func_a.json").write_text('{"function_key": "mod.py::func_a"}')
    (cache_dir / "func_b.json").write_text('{"function_key": "other.py::func_b"}')

    with patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=cache_dir):
        helpers = _make_helpers()
        result_raw = impl_clear_state(helpers, "/test", "mod.py")
        result = _load_tool_result(result_raw)
    assert result["cleared"] == 1
    assert (cache_dir / "func_b.json").exists() is True


def test_clear_state_handles_corrupt_json(tmp_path):
    cache_dir = tmp_path / "mutation"
    cache_dir.mkdir()
    (cache_dir / "bad.json").write_text("not valid json")

    with patch("mcp_tools._mutation_tools_impl.get_cache_dir", return_value=cache_dir):
        helpers = _make_helpers()
        result_raw = impl_clear_state(helpers, "/test", "something.py")
        result = _load_tool_result(result_raw)
    assert result["cleared"] == 1

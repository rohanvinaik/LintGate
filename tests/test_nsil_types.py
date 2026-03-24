from lintgate.renderers.nsil.types import (
    AgentState,
    InferenceSnapshot,
    SafetyBounds,
    SystemContext,
    UserIntent,
)

def _load_tool_result(json_str):
    import json as _j, os as _os
    r = _j.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and _os.path.isfile(r.get("file","")):
        with open(r["file"]) as f: return _j.loads(f.read())
    return r



def test_nsil_system_context():
    ctx = SystemContext(project_root="/test")
    assert ctx.active_branch == "main"
    assert not ctx.has_uncommitted_changes
    assert ctx.language_ecosystem == []


def test_nsil_user_intent():
    intent = UserIntent(primary_goal="refactor")
    assert intent.confidence_level == 1.0
    assert intent.constraints == []


def test_nsil_agent_state():
    state = AgentState(active_task="writing tests")
    assert state.current_mode == "planning"
    assert state.blocked_on is None
    assert state.memory_pressure == 0.0


def test_nsil_safety_bounds():
    bounds = SafetyBounds()
    assert bounds.max_files_modified == 10
    assert bounds.forbidden_paths == []
    assert bounds.allowed_commands == []
    assert bounds.require_approval_for == []


def test_nsil_inference_snapshot():
    snap = InferenceSnapshot()
    assert snap.snapshot_id.startswith("snap_")
    assert snap.context.project_root == ""
    assert snap.agent_state.current_mode == "planning"


# ── NSIL MCP tool tests ──────────────────────────────────────────────


def test_nsil_inference_snapshot_no_session():
    import json
    from unittest import mock

    from lintgate.controlplane.types import ControlPlaneConfig
    from mcp_tools.nsil_tools import register

    mcp = mock.MagicMock()
    mcp.tool = lambda *args, **kwargs: lambda f: f
    helpers = {
        "_validate_project_root": lambda p: "/tmp",
        "_json_dumps": lambda x: json.dumps(x),
    }
    tools = register(mcp, helpers)
    snapshot_tool = tools["nsil_inference_snapshot"]

    with (
        mock.patch(
            "mcp_tools.nsil_tools.load_controlplane_config",
            return_value=ControlPlaneConfig(),
        ),
        mock.patch("mcp_tools.nsil_tools.load_session", return_value=None),
    ):
        result = snapshot_tool("/tmp")
        assert "error" in json.loads(result)


def test_nsil_inference_snapshot_with_session():
    import json
    from unittest import mock

    from lintgate.controlplane.session_memory import SessionMemory
    from lintgate.controlplane.types import ControlPlaneConfig
    from mcp_tools.nsil_tools import register

    mcp = mock.MagicMock()
    mcp.tool = lambda *args, **kwargs: lambda f: f
    helpers = {
        "_validate_project_root": lambda p: "/tmp",
        "_json_dumps": lambda x: json.dumps(x),
    }
    tools = register(mcp, helpers)
    snapshot_tool = tools["nsil_inference_snapshot"]

    session = SessionMemory(project_root="/tmp")

    with (
        mock.patch(
            "mcp_tools.nsil_tools.load_controlplane_config",
            return_value=ControlPlaneConfig(),
        ),
        mock.patch("mcp_tools.nsil_tools.load_session", return_value=session),
    ):
        result = snapshot_tool("/tmp")
        parsed = _load_tool_result(result)
        assert parsed["snapshot_id"].startswith("snap_")
        assert parsed["context"]["project_root"] == "/tmp"
        assert parsed["agent_state"]["current_mode"] == "planning"

import json
from unittest import mock

from lintgate.controlplane.session_memory import SessionMemory
from lintgate.controlplane.types import ControlPlaneConfig
from mcp_tools.nsil_tools import register


def test_nsil_inference_snapshot_no_session():
    mcp = mock.MagicMock()
    mcp.tool = lambda *args, **kwargs: lambda f: f
    helpers = {"_validate_project_root": lambda p: "/tmp", "_json_dumps": lambda x: json.dumps(x)}
    tools = register(mcp, helpers)
    snapshot_tool = tools["nsil_inference_snapshot"]

    with (
        mock.patch(
            "mcp_tools.nsil_tools.load_controlplane_config", return_value=ControlPlaneConfig()
        ),
        mock.patch("mcp_tools.nsil_tools.load_session", return_value=None),
    ):
        result = snapshot_tool("/tmp")
        assert "error" in json.loads(result)


def test_nsil_inference_snapshot_with_session():
    mcp = mock.MagicMock()
    mcp.tool = lambda *args, **kwargs: lambda f: f
    helpers = {"_validate_project_root": lambda p: "/tmp", "_json_dumps": lambda x: json.dumps(x)}
    tools = register(mcp, helpers)
    snapshot_tool = tools["nsil_inference_snapshot"]

    session = SessionMemory(project_root="/tmp")

    with (
        mock.patch(
            "mcp_tools.nsil_tools.load_controlplane_config", return_value=ControlPlaneConfig()
        ),
        mock.patch("mcp_tools.nsil_tools.load_session", return_value=session),
    ):
        result = snapshot_tool("/tmp")
        parsed = json.loads(result)
        assert parsed["snapshot_id"].startswith("snap_")
        assert parsed["context"]["project_root"] == "/tmp"
        assert parsed["agent_state"]["current_mode"] == "planning"

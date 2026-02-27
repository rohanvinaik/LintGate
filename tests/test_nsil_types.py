from lintgate.renderers.nsil.types import (
    AgentState,
    InferenceSnapshot,
    SafetyBounds,
    SystemContext,
    UserIntent,
)


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

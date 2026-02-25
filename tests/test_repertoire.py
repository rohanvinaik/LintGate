from lintgate.orchestration.repertoire import RepertoireManager


def test_repertoire_capture_resolution():
    session_memory = {
        "action_history": [
            {"tool": "Bash", "intent": "execute", "sig": "ls"},
            {"tool": "Read", "intent": "inspect", "sig": "foo.py"},
            {"tool": "Write", "intent": "modify", "sig": "foo.py"},
            {"tool": "Bash", "intent": "verify", "sig": "pytest"},
        ],
        "active_finding_history": {},
    }
    mgr = RepertoireManager(session_memory)

    # 1. Start with a finding
    mgr.track_findings({"approach_cycling"}, event_counter=10, action_history_len=1)
    assert "approach_cycling" in session_memory["active_finding_history"]

    # 2. Add more actions and then resolve it (remove from current findings)
    mgr.track_findings(set(), event_counter=11, action_history_len=4)

    # Check that it's no longer in active history
    assert "approach_cycling" not in session_memory["active_finding_history"]

    # Check that it's in repertoire
    assert len(session_memory["resolution_repertoire"]) == 1
    record = session_memory["resolution_repertoire"][0]
    assert record["finding_kind"] == "approach_cycling"
    # Resolution steps should be actions from idx 0 to 4
    assert len(record["resolution_steps"]) == 4
    assert record["resolution_steps"][-1]["intent"] == "verify"


def test_repertoire_hint_generation():
    session_memory = {
        "resolution_repertoire": [
            {
                "finding_kind": "approach_cycling",
                "resolution_steps": [
                    {"intent": "inspect"},
                    {"intent": "modify"},
                    {"intent": "verify"},
                ],
            }
        ]
    }
    mgr = RepertoireManager(session_memory)
    hint = mgr.get_resolution_hint("approach_cycling")
    assert hint == "Previously resolved via: inspect -> modify -> verify"


def test_repertoire_hint_no_match():
    mgr = RepertoireManager({})
    assert mgr.get_resolution_hint("unknown") is None

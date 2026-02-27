from lintgate.orchestration.workflows import get_workflow_for_intent


def test_get_workflow_for_intent_valid():
    wf = get_workflow_for_intent("implement_issue")
    assert len(wf) > 0
    assert "step" in wf[0]
    assert "tool" in wf[0]
    assert "when" in wf[0]
    assert "args_hint" in wf[0]
    assert "do_not" in wf[0]


def test_get_workflow_for_intent_invalid():
    wf = get_workflow_for_intent("magic")
    assert wf == []


def test_get_workflow_for_intent_none():
    wf = get_workflow_for_intent(None)
    assert wf == []

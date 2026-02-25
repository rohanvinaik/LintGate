import json
from unittest.mock import MagicMock

from lintgate.controlplane.session_memory import SessionMemory, _apply_potential_transfer_packet
from lintgate.orchestration.continuity import generate_transfer_packet


def test_generate_transfer_packet():
    bc = MagicMock()
    bc.compliance_rate = 0.85

    hyp1 = MagicMock()
    hyp1.id = "h1"
    hyp1.claim = "Theory A"
    hyp1.confidence = 0.9
    hyp1.status = "confirmed"

    bc.hypotheses = [hyp1]
    bc.nudges.pending_nudge_signals = ["approach_cycling"]
    bc.get.return_value = []  # No resolution_repertoire for now

    session = MagicMock()
    session.session_id = "test_sid"
    session.behavior_compass = bc

    packet = generate_transfer_packet(session)
    assert packet.session_id == "test_sid"
    assert packet.compliance_rate == 0.85
    assert len(packet.confirmed_hypotheses) == 1
    assert packet.confirmed_hypotheses[0]["id"] == "h1"

    json_str = packet.to_json()
    data = json.loads(json_str)
    assert data["sid"] == "test_sid"
    assert data["comp"] == 0.85


def test_apply_transfer_packet(tmp_path):
    project_root = str(tmp_path)
    handoff_file = tmp_path / ".lintgate_handoff.json"

    packet_data = {
        "sid": "new_sid",
        "comp": 0.5,
        "hyps": [{"id": "h_trans", "clm": "Transferred", "conf": 1.0}],
        "active": ["stale_model"],
    }
    handoff_file.write_text(json.dumps(packet_data))

    session = SessionMemory(project_root=project_root)
    _apply_potential_transfer_packet(session, project_root)

    bc = session.behavior_compass
    assert bc["compliance_rate"] == 0.5
    assert bc["pending_nudge_signals"] == ["stale_model"]
    assert len(bc["hypotheses"]) == 1
    assert bc["hypotheses"][0]["id"] == "h_trans"

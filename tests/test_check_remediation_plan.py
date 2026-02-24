from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_remediation_plan.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_remediation_plan", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALID_PR_BODY = """
### Gate Modification Remediation Plan

#### Gate Graph Diff
- local pre-push gates: unchanged
- PR required checks: unchanged
- main-only checks: unchanged

#### Dependency Impacts
- none

#### Expected Check Outcomes
- all required checks remain green

#### Rollback Strategy
- revert commit

- [x] I have evaluated the impact on ALL gate contracts
- [x] I have ensured parity between local preflight and CI
- [x] I have considered fallback behavior for legacy clients
""".strip()


def test_skips_non_gate_changes(monkeypatch, capsys) -> None:
    module = _load_module()
    monkeypatch.setenv("PR_BODY", "")
    monkeypatch.setenv("CHANGED_FILES_JSON", '["README.md"]')
    monkeypatch.delenv("CHANGED_FILES", raising=False)

    code = module.main()
    out = capsys.readouterr().out
    assert code == 0
    assert "Remediation plan not required" in out


def test_no_changed_files_is_non_blocking(monkeypatch, capsys) -> None:
    module = _load_module()
    monkeypatch.setenv("PR_BODY", "")
    monkeypatch.delenv("CHANGED_FILES_JSON", raising=False)
    monkeypatch.delenv("CHANGED_FILES", raising=False)

    code = module.main()
    out = capsys.readouterr().out
    assert code == 0
    assert "No changed files provided." in out


def test_fails_gate_changes_without_required_template(monkeypatch, capsys) -> None:
    module = _load_module()
    monkeypatch.setenv("PR_BODY", "")
    monkeypatch.setenv("CHANGED_FILES_JSON", '["scripts/ship_main.py"]')
    monkeypatch.delenv("CHANGED_FILES", raising=False)

    code = module.main()
    out = capsys.readouterr().out
    assert code == 1
    assert "Missing required remediation plan fields" in out
    assert "Use template: docs/templates/gate_modification_remediation_plan.md" in out


def test_passes_when_template_sections_and_checks_present(monkeypatch, capsys) -> None:
    module = _load_module()
    monkeypatch.setenv("PR_BODY", VALID_PR_BODY)
    monkeypatch.setenv("CHANGED_FILES_JSON", '[".githooks/pre-push", "scripts/ship_main.py"]')
    monkeypatch.delenv("CHANGED_FILES", raising=False)

    code = module.main()
    out = capsys.readouterr().out
    assert code == 0
    assert "Remediation plan acknowledged. Proceeding." in out


def test_falls_back_to_legacy_changed_files_env(monkeypatch, capsys) -> None:
    module = _load_module()
    monkeypatch.setenv("PR_BODY", VALID_PR_BODY)
    monkeypatch.setenv("CHANGED_FILES_JSON", "{bad-json")
    monkeypatch.setenv("CHANGED_FILES", "scripts/ship_main.py")

    code = module.main()
    out = capsys.readouterr().out
    assert code == 0
    assert "falling back to CHANGED_FILES" in out

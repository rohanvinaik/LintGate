import json
import os
import re
import sys
from typing import Final

GATE_PATH_PREFIXES: Final[tuple[str, ...]] = (
    ".github/workflows/",
    "lintgate/channels/",
    "lintgate/controlplane/",
)

GATE_PATH_EXACT: Final[set[str]] = {
    ".githooks/pre-push",
    "scripts/ship_main.py",
    "gate_contract.yaml",
}

REQUIRED_SECTIONS: Final[dict[str, str]] = {
    "heading": r"^###\s*Gate Modification Remediation Plan\s*$",
    "gate_graph": r"^####\s*Gate Graph Diff\s*$",
    "dependency": r"^####\s*Dependency Impacts\s*$",
    "expected_outcomes": r"^####\s*Expected Check Outcomes\s*$",
    "rollback": r"^####\s*Rollback Strategy\s*$",
}

REQUIRED_CHECKS: Final[dict[str, str]] = {
    "impact": r"\[x\]\s*I have evaluated the impact on ALL gate contracts",
    "parity": r"\[x\]\s*I have ensured parity between local preflight and CI",
    "fallback": r"\[x\]\s*I have considered fallback behavior for legacy clients",
}

TEMPLATE_PATH: Final[str] = "docs/templates/gate_modification_remediation_plan.md"


def _parse_changed_files(changed_files_json: str, changed_files_legacy: str) -> list[str]:
    """Parse changed files from JSON list (preferred) or legacy whitespace string."""
    files: list[str] = []
    if changed_files_json:
        try:
            parsed = json.loads(changed_files_json)
            if isinstance(parsed, list):
                files.extend(str(item) for item in parsed if isinstance(item, str) and item.strip())
        except json.JSONDecodeError:
            print("[validator] Warning: CHANGED_FILES_JSON is invalid JSON; falling back to CHANGED_FILES.")

    if not files and changed_files_legacy:
        files.extend(part for part in changed_files_legacy.split() if part.strip())

    # Deduplicate while preserving order.
    return list(dict.fromkeys(files))


def _is_gate_relevant(path: str) -> bool:
    return path in GATE_PATH_EXACT or any(path.startswith(prefix) for prefix in GATE_PATH_PREFIXES)


def _missing_pr_requirements(pr_body: str) -> list[str]:
    missing: list[str] = []
    section_flags = re.IGNORECASE | re.MULTILINE
    for key, pattern in REQUIRED_SECTIONS.items():
        if not re.search(pattern, pr_body, section_flags):
            missing.append(key)
    for key, pattern in REQUIRED_CHECKS.items():
        if not re.search(pattern, pr_body, re.IGNORECASE):
            missing.append(key)
    return missing


def _print_required_template() -> None:
    print("### Gate Modification Remediation Plan")
    print("#### Gate Graph Diff")
    print("- local pre-push gates:")
    print("- PR required checks:")
    print("- main-only checks:")
    print("#### Dependency Impacts")
    print("- ")
    print("#### Expected Check Outcomes")
    print("- ")
    print("#### Rollback Strategy")
    print("- ")
    print("- [ ] I have evaluated the impact on ALL gate contracts")
    print("- [ ] I have ensured parity between local preflight and CI")
    print("- [ ] I have considered fallback behavior for legacy clients")


def main() -> int:
    """Validate PR bodies for impact assessment on modified gate/controlplane paths."""
    pr_body = os.environ.get("PR_BODY", "")
    changed_files_json = os.environ.get("CHANGED_FILES_JSON", "")
    changed_files_legacy = os.environ.get("CHANGED_FILES", "")

    changed_files = _parse_changed_files(changed_files_json, changed_files_legacy)
    if not changed_files:
        print("[validator] No changed files provided.")
        return 0

    if not any(_is_gate_relevant(path) for path in changed_files):
        print("[validator] No core gate or CI files modified. Remediation plan not required.")
        return 0

    print("[validator] Core gate files modified. Checking PR body for remediation plan...")
    missing = _missing_pr_requirements(pr_body)
    if missing:
        print("\n[ERROR] Missing required remediation plan fields.")
        print("Your PR modifies core routing, validation, or gate logic.")
        print(f"Use template: {TEMPLATE_PATH}\n")
        _print_required_template()
        print(f"\nMissing items: {', '.join(missing)}")
        return 1

    print("[validator] Remediation plan acknowledged. Proceeding.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

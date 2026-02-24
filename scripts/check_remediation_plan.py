import os
import re
import sys


def main() -> int:
    """Validate PR bodies for impact assessment on modified gate/controlplane paths."""
    pr_body = os.environ.get("PR_BODY", "")
    changed_files_str = os.environ.get("CHANGED_FILES", "")

    if not changed_files_str:
        print("[validator] No changed files provided.")
        return 0

    changed_files = changed_files_str.split()
    gate_relevant = False

    for file in changed_files:
        if (
            file.startswith(".github/workflows/")
            or file == ".githooks/pre-push"
            or file == "scripts/ship_main.py"
            or file == "gate_contract.yaml"
            or file.startswith("lintgate/channels/")
            or file.startswith("lintgate/controlplane/")
        ):
            gate_relevant = True
            break

    if not gate_relevant:
        print("[validator] No core gate or CI files modified. Remediation plan not required.")
        return 0

    print("[validator] Core gate files modified. Checking PR body for remediation plan...")

    # Look for the required checklist items in the PR body
    checks = {
        "impact": r"\[x\]\s*I have evaluated the impact on ALL gate contracts",
        "parity": r"\[x\]\s*I have ensured parity between local preflight and CI",
        "fallback": r"\[x\]\s*I have considered fallback behavior for legacy clients",
    }

    missing = []
    for key, pattern in checks.items():
        if not re.search(pattern, pr_body, re.IGNORECASE):
            missing.append(key)

    if missing:
        print("\n[ERROR] Missing required remediation plan acknowledgments!")
        print("Your PR modifies core routing, validation, or gate logic.")
        print("You must include the following checklist in your PR description and check all boxes:\n")
        print("### Gate Modification Remediation Plan")
        print("- [ ] I have evaluated the impact on ALL gate contracts")
        print("- [ ] I have ensured parity between local preflight and CI")
        print("- [ ] I have considered fallback behavior for legacy clients\n")
        print(f"Missing items: {', '.join(missing)}")
        return 1

    print("[validator] Remediation plan acknowledged. Proceeding.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

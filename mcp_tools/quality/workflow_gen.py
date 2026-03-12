"""Workflow generation helpers.

Generates GitHub Actions workflow YAML for quality infrastructure.
Each workflow is defined as structured data and rendered by a shared
builder, eliminating the duplicated string-list boilerplate that
SonarCloud flagged as code smells.
"""

from __future__ import annotations

from typing import Any

# ── Pinned action SHAs (single source of truth) ─────────────────────

_ACTIONS = {
    "checkout": "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4",
    "setup_python": "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5",
    "upload_artifact": "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4",
    "download_artifact": "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4",
    "codeql_init": "github/codeql-action/init@45580472a5bb82c4681c4ac726cfdb60060c2ee1 # v3",
    "codeql_autobuild": "github/codeql-action/autobuild@45580472a5bb82c4681c4ac726cfdb60060c2ee1 # v3",
    "codeql_analyze": "github/codeql-action/analyze@45580472a5bb82c4681c4ac726cfdb60060c2ee1 # v3",
    "codeql_upload_sarif": "github/codeql-action/upload-sarif@45580472a5bb82c4681c4ac726cfdb60060c2ee1 # v3",
    "scorecard": "ossf/scorecard-action@62b2cac7ed8198b15735ed49ab1e5cf35480ba46 # v2.4.0",
    "clusterfuzz_build": "google/clusterfuzzlite/actions/build_fuzzers@52ecc61cb587ee99c26825a112a21abf19c7448c # main",
    "clusterfuzz_run": "google/clusterfuzzlite/actions/run_fuzzers@52ecc61cb587ee99c26825a112a21abf19c7448c # main",
    "pypi_publish": "pypa/gh-action-pypi-publish@ed0c53931b1dc9bd32cbe73a98c7f6766f8a527e # release/v1",
    "sigstore": "sigstore/gh-action-sigstore-python@a5caf349bc536fbef3668a10ed7f5cd309a4b53d # v3.2.0",
    "pip_audit": "pypa/gh-action-pip-audit@v1.0.8",
    "sonarcloud": "sonarsource/sonarcloud-github-action@v2",
    "qlty_install": "qltysh/qlty-action/install@0814173ae3b13074fc896ca0e8e6d631c8352509 # main",
}

# Constants used by both this module and quality_helpers / onboarding_tools.
# Defined here (the primary consumer) to avoid an import cycle with quality_helpers.
REQUIRED_ARTIFACTS = {
    "codeclimate": ".codeclimate.yml",
    "sonar": "sonar-project.properties",
    "coveragerc": ".coveragerc",
    "gitleaks": ".gitleaks.toml",
    "security_policy": "SECURITY.md",
}

REQUIRED_BADGE_FINGERPRINTS = [
    "actions/workflows/tests.yml/badge.svg",
    "actions/workflows/security-lite.yml/badge.svg",
    "metric=alert_status",
    "metric=coverage",
    "metric=security_rating",
]


# ── Workflow builder ─────────────────────────────────────────────────


def _step_checkout(*, persist_credentials: bool | None = None) -> dict[str, Any]:
    """Checkout step."""
    step: dict[str, Any] = {"name": "Checkout", "uses": _ACTIONS["checkout"]}
    if persist_credentials is not None:
        step["with"] = {"persist-credentials": persist_credentials}
    return step


def _step_setup_python(version: str = "3.11") -> dict[str, Any]:
    """Setup Python step."""
    return {
        "name": "Set up Python",
        "uses": _ACTIONS["setup_python"],
        "with": {"python-version": version},
    }


def _render_step(step: dict[str, Any], indent: int = 6) -> list[str]:
    """Render a single step dict to YAML lines."""
    prefix = " " * indent
    lines: list[str] = []
    lines.append(f"{prefix}- name: {step['name']}")

    if "uses" in step:
        lines.append(f"{prefix}  uses: {step['uses']}")
    if "id" in step:
        lines.append(f"{prefix}  id: {step['id']}")
    if "run" in step:
        run_val = step["run"]
        if "\n" in run_val:
            lines.append(f"{prefix}  run: |")
            for run_line in run_val.split("\n"):
                lines.append(f"{prefix}    {run_line}" if run_line else "")
        else:
            lines.append(f"{prefix}  run: {run_val}")
    if "with" in step:
        lines.append(f"{prefix}  with:")
        for k, v in step["with"].items():
            if isinstance(v, bool):
                lines.append(f"{prefix}    {k}: {'true' if v else 'false'}")
            elif isinstance(v, str) and " " not in str(v) and not str(v).startswith('"'):
                lines.append(f"{prefix}    {k}: {v}")
            else:
                lines.append(f'{prefix}    {k}: "{v}"')
    if "env" in step:
        lines.append(f"{prefix}  env:")
        for k, v in step["env"].items():
            lines.append(f"{prefix}    {k}: {v}")

    return lines


def _render_workflow(
    name: str,
    *,
    on: dict[str, Any],
    permissions: dict[str, str] | str,
    jobs: dict[str, Any],
    concurrency_prefix: str | None = None,
) -> str:
    """Render a complete workflow to YAML string."""
    lines: list[str] = [f"name: {name}", ""]

    # on: block
    lines.append("on:")
    for trigger, config in on.items():
        if config is None:
            lines.append(f"  {trigger}:")
        elif isinstance(config, dict):
            lines.append(f"  {trigger}:")
            for k, v in config.items():
                if isinstance(v, list):
                    lines.append(f"    {k}:")
                    for item in v:
                        lines.append(f"      - {item}")
                else:
                    lines.append(f"    {k}: {v}")
        elif isinstance(config, list):
            lines.append(f"  {trigger}:")
            for item in config:
                if isinstance(item, dict):
                    for k, v in item.items():
                        lines.append(f"    {k}: {v}")
                else:
                    lines.append(f"    - {item}")
    lines.append("")

    # permissions
    if isinstance(permissions, str):
        lines.append(f"permissions: {permissions}")
    else:
        lines.append("permissions:")
        for k, v in permissions.items():
            lines.append(f"  {k}: {v}")
    lines.append("")

    # concurrency
    if concurrency_prefix:
        lines.extend(
            [
                "concurrency:",
                f"  group: {concurrency_prefix}-${{{{ github.workflow }}}}-${{{{ github.ref }}}}",
                "  cancel-in-progress: true",
                "",
            ]
        )

    # jobs
    lines.append("jobs:")
    for job_id, job in jobs.items():
        lines.append(f"  {job_id}:")
        lines.append(f"    name: {job['name']}")
        if "needs" in job:
            lines.append(f"    needs: {job['needs']}")
        lines.append(f"    runs-on: {job.get('runs-on', 'ubuntu-latest')}")
        if "environment" in job:
            lines.append(f"    environment: {job['environment']}")
        if "permissions" in job:
            lines.append("    permissions:")
            for k, v in job["permissions"].items():
                lines.append(f"      {k}: {v}")
        lines.append("    steps:")
        for step in job["steps"]:
            lines.extend(_render_step(step, indent=6))
            lines.append("")

    # Trim trailing blank lines, ensure single trailing newline
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


# ── Standard trigger/permission combos ───────────────────────────────

_ON_PUSH_PR_DISPATCH: dict[str, Any] = {
    "push": None,
    "pull_request": {"types": "[opened, synchronize, reopened]"},
    "workflow_dispatch": None,
}

_PERMS_READ: dict[str, str] = {"contents": "read"}

_PERMS_READ_SECURITY: dict[str, str] = {"contents": "read", "security-events": "write"}


# ── Workflow generators ──────────────────────────────────────────────


def _generate_scorecard_workflow() -> str:
    """Generate OpenSSF Scorecard GitHub Action workflow."""
    return _render_workflow(
        "OpenSSF Scorecard",
        on={
            "branch_protection_rule": None,
            "schedule": [{"cron": "'30 1 * * 1'  # Weekly Monday 01:30 UTC"}],
            "push": {"branches": "[main, master]"},
            "workflow_dispatch": None,
        },
        permissions="read-all",
        jobs={
            "analysis": {
                "name": "Scorecard Analysis",
                "runs-on": "ubuntu-latest",
                "permissions": {"security-events": "write", "id-token": "write"},
                "steps": [
                    _step_checkout(persist_credentials=False),
                    {
                        "name": "Run Scorecard",
                        "uses": _ACTIONS["scorecard"],
                        "with": {
                            "results_file": "results.sarif",
                            "results_format": "sarif",
                            "publish_results": True,
                        },
                    },
                    {
                        "name": "Upload SARIF",
                        "uses": _ACTIONS["codeql_upload_sarif"],
                        "with": {"sarif_file": "results.sarif"},
                    },
                ],
            }
        },
    )


def _generate_codeql_workflow() -> str:
    """Generate a CodeQL analysis workflow."""
    return _render_workflow(
        "CodeQL",
        on={
            "push": None,
            "pull_request": {"types": "[opened, synchronize, reopened]"},
            "schedule": [{"cron": "'15 3 * * 1'  # Weekly Monday 03:15 UTC"}],
        },
        permissions=_PERMS_READ_SECURITY,
        concurrency_prefix="codeql",
        jobs={
            "analyze": {
                "name": "CodeQL Analysis",
                "runs-on": "ubuntu-latest",
                "steps": [
                    _step_checkout(),
                    {
                        "name": "Initialize CodeQL",
                        "uses": _ACTIONS["codeql_init"],
                        "with": {"languages": "python"},
                    },
                    {"name": "Autobuild", "uses": _ACTIONS["codeql_autobuild"]},
                    {
                        "name": "Perform CodeQL Analysis",
                        "uses": _ACTIONS["codeql_analyze"],
                    },
                ],
            }
        },
    )


def _generate_clusterfuzzlite_workflow() -> str:
    """Generate a ClusterFuzzLite batch fuzzing workflow."""
    return _render_workflow(
        "ClusterFuzzLite",
        on={
            "push": {"branches": "[main]"},
            "pull_request": {"branches": "[main]"},
            "schedule": [{"cron": "'0 6 * * 0'  # Weekly Sunday 06:00 UTC (batch mode)"}],
            "workflow_dispatch": None,
        },
        permissions=_PERMS_READ_SECURITY,
        concurrency_prefix="cif",
        jobs={
            "fuzz": {
                "name": "ClusterFuzzLite Batch Fuzzing",
                "runs-on": "ubuntu-latest",
                "steps": [
                    _step_checkout(),
                    {
                        "name": "Build fuzzers",
                        "id": "build",
                        "uses": _ACTIONS["clusterfuzz_build"],
                        "with": {"language": "python"},
                    },
                    {
                        "name": "Run fuzzers",
                        "id": "run",
                        "uses": _ACTIONS["clusterfuzz_run"],
                        "with": {
                            "github-token": "${{ secrets.GITHUB_TOKEN }}",
                            "fuzz-seconds": "300",
                            "mode": "batch",
                        },
                    },
                ],
            }
        },
    )


def _generate_pypi_publish_workflow() -> str:
    """Generate a PyPI publish workflow."""
    return _render_workflow(
        "Publish to PyPI",
        on={
            "release": {"types": "[published]"},
            "workflow_dispatch": None,
        },
        permissions=_PERMS_READ,
        jobs={
            "build": {
                "name": "Build distribution",
                "runs-on": "ubuntu-latest",
                "steps": [
                    _step_checkout(),
                    _step_setup_python(),
                    {
                        "name": "Install build tools",
                        "run": "python -m pip install --upgrade pip==25.0.1 build",
                    },
                    {"name": "Build sdist and wheel", "run": "python -m build"},
                    {
                        "name": "Upload dist artifacts",
                        "uses": _ACTIONS["upload_artifact"],
                        "with": {"name": "dist", "path": "dist/"},
                    },
                ],
            },
            "publish": {
                "name": "Publish to PyPI",
                "needs": "build",
                "runs-on": "ubuntu-latest",
                "environment": "pypi",
                "permissions": {"id-token": "write"},
                "steps": [
                    {
                        "name": "Download dist artifacts",
                        "uses": _ACTIONS["download_artifact"],
                        "with": {"name": "dist", "path": "dist/"},
                    },
                    {
                        "name": "Publish to PyPI (trusted publisher)",
                        "uses": _ACTIONS["pypi_publish"],
                    },
                ],
            },
            "sign": {
                "name": "Sign with Sigstore",
                "needs": "publish",
                "runs-on": "ubuntu-latest",
                "permissions": {"id-token": "write", "contents": "read"},
                "steps": [
                    {
                        "name": "Download dist artifacts",
                        "uses": _ACTIONS["download_artifact"],
                        "with": {"name": "dist", "path": "dist/"},
                    },
                    {
                        "name": "Sign with Sigstore",
                        "uses": _ACTIONS["sigstore"],
                        "with": {"inputs": "dist/*.tar.gz dist/*.whl"},
                    },
                    {
                        "name": "Upload signatures",
                        "uses": _ACTIONS["upload_artifact"],
                        "with": {"name": "signatures", "path": "dist/*.sigstore.json"},
                    },
                ],
            },
        },
    )


def _generate_quality_infra_gate_workflow() -> str:
    """Generate the quality infrastructure gate CI workflow."""
    file_checks: list[str] = []
    for _name, rel_path in REQUIRED_ARTIFACTS.items():
        file_checks.append(
            f'if [ ! -e "{rel_path}" ]; then echo "MISSING: {rel_path}"; MISSING=$((MISSING+1)); fi'
        )

    fp_checks: list[str] = []
    for fp in REQUIRED_BADGE_FINGERPRINTS:
        escaped = fp.replace(".", "\\.").replace("/", "\\/")
        fp_checks.append(
            f'if ! grep -q "{escaped}" README.md 2>/dev/null; then '
            f'echo "BADGE MISSING: {fp}"; MISSING=$((MISSING+1)); fi'
        )

    check_lines = ["MISSING=0"]
    check_lines.extend(file_checks)
    check_lines.extend(fp_checks)
    check_lines.extend(
        [
            'if [ "$MISSING" -gt 0 ]; then',
            '  echo ""',
            '  echo "Quality infrastructure is incomplete ($MISSING items missing)."',
            '  echo "Fix: run setup_github_quality(path=..., write=True)"',
            "  exit 1",
            "fi",
            'echo "Quality infrastructure: complete"',
        ]
    )

    return _render_workflow(
        "Quality Infrastructure Gate",
        on=_ON_PUSH_PR_DISPATCH,
        permissions=_PERMS_READ,
        concurrency_prefix="quality-infra",
        jobs={
            "gate": {
                "name": "Quality Infrastructure Gate",
                "runs-on": "ubuntu-latest",
                "steps": [
                    _step_checkout(),
                    {
                        "name": "Check quality infrastructure completeness",
                        "run": "\n".join(check_lines),
                    },
                ],
            }
        },
    )


def _generate_qlty_workflow() -> str:
    """Generate a GitHub Actions workflow for qlty checks."""
    init_script = (
        "if ! qlty check --all --dry-run >/dev/null 2>&1; then\n"
        "  qlty init --skip-plugins 2>/dev/null || true\n"
        "fi"
    )
    return _render_workflow(
        "Qlty Analysis",
        on=_ON_PUSH_PR_DISPATCH,
        permissions=_PERMS_READ,
        concurrency_prefix="qlty",
        jobs={
            "qlty": {
                "name": "Qlty Check",
                "runs-on": "ubuntu-latest",
                "steps": [
                    _step_checkout(),
                    {"name": "Install qlty", "uses": _ACTIONS["qlty_install"]},
                    {"name": "Initialize qlty (if needed)", "run": init_script},
                    {"name": "Run qlty checks", "run": "qlty check --all"},
                ],
            }
        },
    )


def _generate_security_workflow() -> str:
    """Generate a lightweight security scanning workflow."""
    return _render_workflow(
        "Security Lite",
        on=_ON_PUSH_PR_DISPATCH,
        permissions=_PERMS_READ_SECURITY,
        concurrency_prefix="security-lite",
        jobs={
            "security": {
                "name": "Security Scanning",
                "runs-on": "ubuntu-latest",
                "steps": [
                    _step_checkout(),
                    _step_setup_python(),
                    {"name": "Install Bandit", "run": "pip install bandit"},
                    {"name": "Run Bandit", "run": "bandit -r . -ll -ii"},
                    {
                        "name": "Run pip-audit",
                        "uses": _ACTIONS["pip_audit"],
                        "with": {"inputs": "requirements.txt"},
                    },
                ],
            }
        },
    )


def _generate_tests_workflow() -> str:
    """Generate a comprehensive test workflow with coverage."""
    install_script = (
        "python -m pip install --upgrade pip\n"
        "pip install pytest pytest-cov\n"
        "if [ -f requirements.txt ]; then pip install -r requirements.txt; fi"
    )
    return _render_workflow(
        "tests",
        on={
            "push": {"branches": "[main, master]"},
            "pull_request": {"branches": "[main, master]"},
            "workflow_dispatch": None,
        },
        permissions=_PERMS_READ,
        jobs={
            "test": {
                "name": "Run tests",
                "runs-on": "ubuntu-latest",
                "steps": [
                    _step_checkout(),
                    _step_setup_python(),
                    {"name": "Install dependencies", "run": install_script},
                    {
                        "name": "Run tests with coverage",
                        "run": "pytest --cov=./ --cov-report=xml",
                    },
                    {
                        "name": "Upload coverage to SonarCloud",
                        "uses": _ACTIONS["sonarcloud"],
                        "env": {
                            "GITHUB_TOKEN": "${{ secrets.GITHUB_TOKEN }}",
                            "SONAR_TOKEN": "${{ secrets.SONAR_TOKEN }}",
                        },
                    },
                ],
            }
        },
    )

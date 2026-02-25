import json
import os
import sys

# Add project root to sys.path
project_root = "/Users/rohanvinaik/tools/lintgate"
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from mcp_tools.controlplane_tools import _impl_controlplane_run  # noqa: E402


class MockHelpers:
    def __getitem__(self, key):
        if key == "_validate_project_root":
            return lambda p: os.path.abspath(p)
        if key == "_collect_python_files":
            from mcp_server import _collect_python_files

            return _collect_python_files
        if key == "_build_cp_full_details":
            from mcp_server import _build_cp_full_details

            return _build_cp_full_details
        if key == "_json_dumps":
            return lambda d, output_mode="compact": json.dumps(d, indent=2)
        if key == "_build_onboarding_status":
            from mcp_server import _build_onboarding_status

            return _build_onboarding_status
        return None


helpers = MockHelpers()

try:
    result_json = _impl_controlplane_run(
        path=project_root,
        channels="lint,tests,deps,git,behavior,structure,performance,test_effectiveness,mutation",
        strictness="strict",
        scope="changed",
        files=None,
        max_findings=50,
        output_budget="standard",
        helpers=helpers,
    )
    print(result_json)
except Exception as e:
    import traceback

    print(f"Error: {e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)

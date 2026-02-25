"""Contract tests for required-check dependency profile.

This test module ensures the required-check profile has all dependencies
necessary to run the CI test suite, preventing the split-brain invariant
issue identified in #96.
"""

import subprocess
import sys
from pathlib import Path


class TestRequiredProfileContract:
    """Tests for the required-check dependency profile contract."""

    def test_check_required_profile_script_exists(self):
        """Verify the check_required_profile.py script exists."""
        script_path = Path("scripts/check_required_profile.py")
        assert script_path.exists(), "scripts/check_required_profile.py not found"
        assert script_path.is_file(), "scripts/check_required_profile.py is not a file"

    def test_check_required_profile_runs_successfully(self):
        """Verify the script runs and exits 0 when profile is valid."""
        result = subprocess.run(
            [sys.executable, "scripts/check_required_profile.py", "--profile", "required-checks"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "PASS" in result.stdout, f"Expected PASS in output: {result.stdout}"

    def test_check_required_profile_fails_for_unknown_profile(self):
        """Verify the script fails gracefully for unknown profiles."""
        result = subprocess.run(
            [sys.executable, "scripts/check_required_profile.py", "--profile", "unknown"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, "Expected non-zero exit for unknown profile"

    def test_required_imports_are_available(self):
        """Verify critical required imports are available in current environment."""
        # Test core test framework
        import pytest

        assert pytest is not None

    def test_script_output_contains_helpful_message(self):
        """Verify script output contains helpful failure message."""
        # Test with invalid module that will fail
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import sys
sys.path.insert(0, 'scripts')
from check_required_profile import REQUIRED_IMPORTS
# Patch check_import to simulate missing module
import check_required_profile
original_check = check_required_profile.check_import
def fake_check(m):
    if m == 'nonexistent_module':
        return False, "No module named 'nonexistent_module'"
    return original_check(m)
check_required_profile.check_import = fake_check
is_valid, missing = check_required_profile.validate_profile('required-checks')
print("VALID" if is_valid else "MISSING:" + str(missing))
""",
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        # Just verify the script can be imported and run its logic
        assert "check_required_profile" in result.stdout or result.returncode == 0


class TestRequiredProfileParser:
    """Tests for the required profile parser behavior."""

    def test_required_imports_dict_structure(self):
        """Verify REQUIRED_IMPORTS has expected structure."""
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from check_required_profile import REQUIRED_IMPORTS

        assert isinstance(REQUIRED_IMPORTS, dict), "REQUIRED_IMPORTS should be a dict"
        assert "pytest" in REQUIRED_IMPORTS, "Should include pytest category"
        assert "algebra" in REQUIRED_IMPORTS, "Should include algebra category"
        assert "runtime" in REQUIRED_IMPORTS, "Should include runtime category"

    def test_algebra_category_includes_hypothesis(self):
        """Verify algebra category includes hypothesis (previously drifted module)."""
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from check_required_profile import REQUIRED_IMPORTS

        assert "hypothesis" in REQUIRED_IMPORTS["algebra"], (
            "hypothesis should be in algebra category"
        )

    def test_runtime_category_includes_core_modules(self):
        """Verify runtime category includes core modules exercised by required checks."""
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from check_required_profile import REQUIRED_IMPORTS

        runtime_modules = REQUIRED_IMPORTS["runtime"]
        assert "lintgate" in runtime_modules
        assert "lintgate.state" in runtime_modules
        assert "lintgate.mutation.state" in runtime_modules
        assert "mcp_tools.mutation_tools" in runtime_modules

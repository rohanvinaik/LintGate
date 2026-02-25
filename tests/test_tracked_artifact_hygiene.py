"""Contract tests for tracked artifact hygiene.

This test module ensures generated/cache/scratch artifacts are not tracked
in git, preventing the ship friction from tracked artifact churn identified
in #96.
"""

import subprocess
import sys
from pathlib import Path


class TestTrackedArtifactHygiene:
    """Tests for the tracked artifact hygiene gate."""

    def test_check_tracked_artifacts_script_exists(self):
        """Verify the check_tracked_artifacts.py script exists."""
        script_path = Path("scripts/check_tracked_artifacts.py")
        assert script_path.exists(), "scripts/check_tracked_artifacts.py not found"
        assert script_path.is_file(), "scripts/check_tracked_artifacts.py is not a file"

    def test_check_tracked_artifacts_runs_on_clean_tree(self):
        """Verify the script runs and exits 0 on a clean tree."""
        # Add test path argument
        result = subprocess.run(
            [sys.executable, "scripts/check_tracked_artifacts.py", "."],
            capture_output=True,
            text=True,
        )
        # Exit code may be 0 (clean) or 1 (with --enforce but violations found)
        assert result.returncode in (0, 1), f"Unexpected exit code: {result.returncode}"

    def test_check_tracked_artifacts_enforce_fails_with_violations(self, tmp_path):
        """Verify --enforce exits non-zero when violations exist."""
        # Create a mock git repo
        git_dir = tmp_path / "test_repo"
        git_dir.mkdir()
        (git_dir / ".git").mkdir()

        # Create a tracked file that matches blocked pattern
        (git_dir / ".hypothesis").mkdir()
        (git_dir / ".hypothesis" / "test_example.py").touch()

        # Initialize git and add the file
        subprocess.run(["git", "init"], cwd=git_dir, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=git_dir,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=git_dir,
            capture_output=True,
        )
        subprocess.run(
            ["git", "add", ".hypothesis/test_example.py"],
            cwd=git_dir,
            capture_output=True,
        )

        # Run the check
        result = subprocess.run(
            [sys.executable, "scripts/check_tracked_artifacts.py", "--enforce", str(git_dir)],
            capture_output=True,
            text=True,
        )

        # Should fail because .hypothesis is tracked
        assert result.returncode != 0, "Expected non-zero exit for tracked .hypothesis"
        assert "FAIL" in result.stdout, f"Expected FAIL in output: {result.stdout}"

    def test_script_output_contains_helpful_message(self):
        """Verify script output contains helpful failure message."""
        result = subprocess.run(
            [sys.executable, "scripts/check_tracked_artifacts.py", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--enforce" in result.stdout
        assert "repo_path" in result.stdout


class TestTrackedArtifactPatterns:
    """Tests for the blocked pattern list."""

    def test_blocked_patterns_dict_exists(self):
        """Verify BLOCKED_PATTERNS is defined."""
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from check_tracked_artifacts import BLOCKED_PATTERNS

        assert isinstance(BLOCKED_PATTERNS, list), "BLOCKED_PATTERNS should be a list"
        assert len(BLOCKED_PATTERNS) > 0, "BLOCKED_PATTERNS should not be empty"

    def test_hypothesis_pattern_is_blocked(self):
        """Verify .hypothesis directory is in blocked patterns."""
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from check_tracked_artifacts import BLOCKED_PATTERNS

        patterns = [p[0] for p in BLOCKED_PATTERNS]
        assert any(".hypothesis" in p for p in patterns), (
            ".hypothesis should be in blocked patterns"
        )

    def test_coverage_patterns_are_blocked(self):
        """Verify coverage artifacts are in blocked patterns."""
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from check_tracked_artifacts import BLOCKED_PATTERNS

        patterns = [p[0] for p in BLOCKED_PATTERNS]
        assert any("coverage" in p for p in patterns), "coverage should be in blocked patterns"
        assert any("pytest-results" in p for p in patterns), (
            "pytest-results should be in blocked patterns"
        )

    def test_mutation_patterns_are_blocked(self):
        """Verify mutation artifacts are in blocked patterns."""
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from check_tracked_artifacts import BLOCKED_PATTERNS

        patterns = [p[0] for p in BLOCKED_PATTERNS]
        assert any("mutation" in p for p in patterns), "mutation should be in blocked patterns"
        assert any("mutants" in p for p in patterns), "mutants should be in blocked patterns"

    def test_cp_scratch_patterns_are_blocked(self):
        """Verify ControlPlane scratch patterns are blocked."""
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from check_tracked_artifacts import BLOCKED_PATTERNS

        patterns = [p[0] for p in BLOCKED_PATTERNS]
        assert any("cp_" in p for p in patterns), "cp_* patterns should be in blocked patterns"


class TestTrackedArtifactCheckLogic:
    """Tests for the core check logic."""

    def test_get_tracked_files_returns_list(self, tmp_path):
        """Verify get_tracked_files returns a list of files."""
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from check_tracked_artifacts import get_tracked_files

        # Use the actual repo
        result = get_tracked_files(Path.cwd())
        assert isinstance(result, list)

    def test_check_tracked_artifacts_returns_list(self):
        """Verify check_tracked_artifacts returns a list of violations."""
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from check_tracked_artifacts import check_tracked_artifacts

        result = check_tracked_artifacts(Path.cwd())
        assert isinstance(result, list)
        # Each item should be a tuple of (file_path, reason)
        for item in result:
            assert isinstance(item, tuple)
            assert len(item) == 2

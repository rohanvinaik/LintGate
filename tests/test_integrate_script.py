"""Shell-level integration tests for integrate.sh robustness."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path


def _make_integrate_harness(tmp_path: Path) -> Path:
    """Create an isolated repo-shaped harness that can run integrate.sh safely."""
    repo = tmp_path / "repo"
    repo.mkdir()

    source_repo = Path(__file__).resolve().parent.parent
    shutil.copy2(source_repo / "integrate.sh", repo / "integrate.sh")
    os.chmod(repo / "integrate.sh", stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    # Tool count source for integrate.sh.
    (repo / "mcp_server.py").write_text(
        "@mcp.tool()\n"
        "def a():\n"
        "    pass\n\n"
        "@mcp.tool()\n"
        "def b():\n"
        "    pass\n\n"
        "@mcp.tool()\n"
        "def c():\n"
        "    pass\n"
    )

    # Source-of-truth docs target referenced by generated files.
    (repo / "AGENTS.md").write_text("# agents\n")
    return repo


def _run_integrate(repo: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = "/usr/bin:/bin"
    return subprocess.run(
        ["/bin/bash", "integrate.sh", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_integrate_agent_override_generates_codex_context(tmp_path: Path) -> None:
    repo = _make_integrate_harness(tmp_path)
    proc = _run_integrate(repo, tmp_path, "--agent", "codex")
    assert proc.returncode == 0, proc.stderr
    assert (repo / ".codex" / "context.md").exists()


def test_integrate_preserves_literal_backticks_in_cognitive_context(tmp_path: Path) -> None:
    repo = _make_integrate_harness(tmp_path)
    proc = _run_integrate(repo, tmp_path, "--agent", "codex")
    assert proc.returncode == 0, proc.stderr
    assert "command not found" not in proc.stderr

    context = (repo / ".codex" / "context.md").read_text()
    assert "`build_theory_pack`" in context
    assert "`controlplane_run`" in context
    assert "`constraint_check`" in context
    assert "AGENTS.md (3 tools by cognitive mode)" in context

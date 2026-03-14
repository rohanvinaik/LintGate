"""Shell-level integration tests for setup.sh bootstrap behavior."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


def _make_setup_harness(tmp_path: Path) -> Path:
    """Create an isolated repo-shaped harness that can run setup.sh safely."""
    repo = tmp_path / "repo"
    repo.mkdir()

    source_repo = Path(__file__).resolve().parent.parent
    shutil.copy2(source_repo / "setup.sh", repo / "setup.sh")
    os.chmod(repo / "setup.sh", stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    (repo / "integrate.sh").write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\necho "integrate stub"\n'
    )
    os.chmod(repo / "integrate.sh", stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    # Minimal MCP server surface for setup's verification probe.
    (repo / "mcp_server.py").write_text(
        "class _ToolManager:\n"
        "    def __init__(self):\n"
        "        self._tools = [1, 2, 3]\n\n"
        "class _MCP:\n"
        "    def __init__(self):\n"
        "        self._tool_manager = _ToolManager()\n\n"
        "mcp = _MCP()\n"
    )

    pkg = repo / "lintgate"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "context_bootstrap.py").write_text(
        "from pathlib import Path\n\n"
        "def bootstrap_context_files(project_root: str, write: bool = False, **_: object):\n"
        "    files = [\n"
        "        '.claude/CLAUDE.md',\n"
        "        'AGENTS.md',\n"
        "        '.claude/rules/inquiry.md',\n"
        "        '.claude/rules/theory.md',\n"
        "    ]\n"
        "    reports = []\n"
        "    for rel in files:\n"
        "        status = 'planned'\n"
        "        path = Path(project_root) / rel\n"
        "        if write:\n"
        "            path.parent.mkdir(parents=True, exist_ok=True)\n"
        "            path.write_text(f'# generated {rel}\\n')\n"
        "            status = 'written'\n"
        "        reports.append({'relative_path': rel, 'status': status, 'line_count': 1})\n"
        "    return {'files': reports}\n"
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "venv" ]]; then\n'
        '  VENV_DIR="$2"\n'
        '  mkdir -p "$VENV_DIR/bin"\n'
        '  ln -sf "$REAL_PYTHON" "$VENV_DIR/bin/python3"\n'
        "  cat > \"$VENV_DIR/bin/lintgate\" <<'EOF'\n"
        "#!/usr/bin/env bash\n"
        'echo "{}"\n'
        "EOF\n"
        "  cat > \"$VENV_DIR/bin/lintgate-mcp\" <<'EOF'\n"
        "#!/usr/bin/env bash\n"
        "exit 0\n"
        "EOF\n"
        '  chmod +x "$VENV_DIR/bin/lintgate"\n'
        '  chmod +x "$VENV_DIR/bin/lintgate-mcp"\n'
        "  exit 0\n"
        "fi\n"
        'if [[ "${1:-}" == "pip" ]]; then\n'
        "  exit 0\n"
        "fi\n"
        'echo "fake uv: unsupported args: $*" >&2\n'
        "exit 1\n"
    )
    os.chmod(fake_bin / "uv", stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    return repo


def _run_setup(repo: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["REAL_PYTHON"] = sys.executable
    env["PATH"] = f"{tmp_path / 'bin'}:{env.get('PATH', '')}"
    return subprocess.run(
        ["bash", "setup.sh", "--minimal"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_setup_bootstraps_when_only_claude_exists(tmp_path: Path) -> None:
    repo = _make_setup_harness(tmp_path)

    # Partial state: CLAUDE exists, inquiry/theory/agents missing.
    claude = repo / ".claude" / "CLAUDE.md"
    claude.parent.mkdir(parents=True)
    claude.write_text("# existing CLAUDE\n")

    proc = _run_setup(repo, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "Bootstrapping context files" in proc.stdout
    assert "missing:" in proc.stdout
    assert (repo / "AGENTS.md").exists()
    assert (repo / ".claude" / "rules" / "inquiry.md").exists()
    assert (repo / ".claude" / "rules" / "theory.md").exists()


def test_setup_skips_bootstrap_when_all_required_files_exist(tmp_path: Path) -> None:
    repo = _make_setup_harness(tmp_path)

    paths = [
        repo / ".claude" / "CLAUDE.md",
        repo / "AGENTS.md",
        repo / ".claude" / "rules" / "inquiry.md",
        repo / ".claude" / "rules" / "theory.md",
    ]
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("sentinel\n")

    proc = _run_setup(repo, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "Context files exist — skipping bootstrap." in proc.stdout

    # Ensure bootstrap was not invoked (sentinel content remains unchanged).
    for p in paths:
        assert p.read_text() == "sentinel\n"


def test_setup_writes_mcp_console_entrypoint_config(tmp_path: Path) -> None:
    repo = _make_setup_harness(tmp_path)
    proc = _run_setup(repo, tmp_path)
    assert proc.returncode == 0, proc.stderr

    home_mcp = tmp_path / "home" / ".mcp.json"
    project_mcp = repo / ".mcp.json"
    for cfg in (home_mcp, project_mcp):
        data = json.loads(cfg.read_text())
        server = data["mcpServers"]["lintgate"]
        assert server["command"].endswith("/.venv/bin/lintgate-mcp")
        assert server["args"] == []


def test_setup_handles_repo_path_with_single_quote(tmp_path: Path) -> None:
    quoted_root = tmp_path / "root-with-'quote"
    quoted_root.mkdir()
    repo = _make_setup_harness(quoted_root)
    proc = _run_setup(repo, quoted_root)
    assert proc.returncode == 0, proc.stderr
    assert "Setup Complete" in proc.stdout


# ── integrate.sh tests ────────────────────────────────────────────────


def _make_integrate_harness(tmp_path: Path) -> Path:
    """Create an isolated repo-shaped harness that can run integrate.sh safely."""
    repo = tmp_path / "repo"
    repo.mkdir()

    source_repo = Path(__file__).resolve().parent.parent
    shutil.copy2(source_repo / "integrate.sh", repo / "integrate.sh")
    os.chmod(repo / "integrate.sh", stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

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


def test_integrate_preserves_literal_backticks_in_cognitive_context(
    tmp_path: Path,
) -> None:
    repo = _make_integrate_harness(tmp_path)
    proc = _run_integrate(repo, tmp_path, "--agent", "codex")
    assert proc.returncode == 0, proc.stderr
    assert "command not found" not in proc.stderr

    context = (repo / ".codex" / "context.md").read_text()
    assert "`build_theory_pack`" in context
    assert "`controlplane_run`" in context
    assert "`constraint_check`" in context
    assert "AGENTS.md (3 tools by cognitive mode)" in context

"""GitHub project organization and wiki sync tools.

Thin registration wrapper. Implementations live in:
- _gh_helpers.py    — shared CLI / git / wiki helpers
- _gh_organize_impl.py — project_organize_audit, project_organize_apply
- _gh_wiki_impl.py     — project_wiki_sync, project_wiki_read
"""

from __future__ import annotations

import json
import os
from typing import Any

from mcp_tools._disk_helpers import tool_response

from mcp_tools._gh_helpers import (  # noqa: F401
    _GITHUB_REMOTE_RE,
    _clone_wiki,
    _detect_repo,
    _push_wiki,
    _repo_full_name,
    _run_gh,
)
from mcp_tools._gh_organize_impl import (
    impl_project_organize_apply,
    impl_project_organize_audit,
)
from mcp_tools._gh_wiki_impl import (
    _render_compass_wiki_page,  # noqa: F401
    _render_theory_wiki_page,  # noqa: F401
    _split_design_md,  # noqa: F401
    impl_project_wiki_read,
    impl_project_wiki_sync,
)

# Re-export old implementation names for backward compatibility
_impl_project_organize_audit = impl_project_organize_audit
_impl_project_organize_apply = impl_project_organize_apply
_impl_project_wiki_sync = impl_project_wiki_sync
_impl_project_wiki_read = impl_project_wiki_read


# ─── Registration ─────────────────────────────────────────────────────────


def register(mcp: Any, helpers: Any) -> dict[str, Any]:
    """Register GitHub organization tools on the shared MCP instance."""

    @mcp.tool()
    def project_organize_audit(
        path: str,
    ) -> str:
        """Audit GitHub project organization and report gaps.

        WHEN TO USE: When starting a project or reviewing its GitHub setup.
        Checks for issue templates, labels, milestones, and wiki status.

        Returns structured gap report with codes ORG001-ORG007.

        Example: project_organize_audit(path="/my/project")

        Args:
            path: Project root path.
        """
        project_root = helpers["_validate_project_root"](path)
        result = impl_project_organize_audit(path, helpers)
        n = len(result.get("findings", []))
        return tool_response(result, "project_organize_audit", project_root, f"Organize audit: {n} findings.")

    @mcp.tool()
    def project_organize_apply(
        path: str,
        actions: list[str] | None = None,
        write: bool = False,
    ) -> str:
        """Apply organization changes (labels, milestones). Dry-run by default.

        WHEN TO USE: After project_organize_audit identifies gaps. Use with
        write=False first to preview changes, then write=True to apply.

        Actions: 'labels' (create missing P0-P3 and type: labels),
        'milestones' (create milestone from issue cluster analysis).

        Example: project_organize_apply(path="/my/project")
        Example: project_organize_apply(path="/my/project", write=True)

        Args:
            path: Project root path.
            actions: List of action types to apply. Default: ['labels', 'milestones'].
            write: If False (default), dry-run showing what would happen.
        """
        project_root = helpers["_validate_project_root"](path)
        result = impl_project_organize_apply(path, actions, write, helpers)
        n = len(result.get("changes", []))
        return tool_response(result, "project_organize_apply", project_root, f"Organize apply: {n} changes.")

    @mcp.tool()
    def project_wiki_sync(
        path: str,
        scope: str = "theory",
        write: bool = False,
    ) -> str:
        """Sync theory/compass/design data to GitHub wiki pages. Dry-run by default.

        WHEN TO USE: To publish project theory, compass, or design docs to the
        GitHub wiki. Use scope='all' for full sync.

        Scopes: 'theory' (theory profile), 'compass' (compass axes),
        'design' (split docs/design.md by headings), 'all' (all scopes).

        Requires wiki to be enabled on the repo with at least one initial page.

        Example: project_wiki_sync(path="/my/project", scope="all")
        Example: project_wiki_sync(path="/my/project", scope="theory", write=True)

        Args:
            path: Project root path.
            scope: What to sync: 'theory', 'compass', 'design', or 'all'.
            write: If False (default), show what pages would be generated.
        """
        project_root = helpers["_validate_project_root"](path)
        result = impl_project_wiki_sync(path, scope, write, helpers)
        n = len(result.get("pages", []))
        return tool_response(result, "project_wiki_sync", project_root, f"Wiki sync: {n} pages.")

    @mcp.tool()
    def project_wiki_read(
        path: str,
        page: str = "Home",
    ) -> str:
        """Read a GitHub wiki page. Checks local clone first, then remote.

        WHEN TO USE: To read wiki content for theory extraction integration.
        Wiki content can feed back into theory claims via the .lintgate/wiki/
        local clone.

        Example: project_wiki_read(path="/my/project")
        Example: project_wiki_read(path="/my/project", page="Theory-Profile")

        Args:
            path: Project root path.
            page: Wiki page name (without .md extension). Default: 'Home'.
        """
        project_root = helpers["_validate_project_root"](path)
        result = impl_project_wiki_read(path, page, helpers)
        return tool_response(result, "project_wiki_read", project_root, f"{page} content retrieved.")

    return {
        "project_organize_audit": project_organize_audit,
        "project_organize_apply": project_organize_apply,
        "project_wiki_sync": project_wiki_sync,
        "project_wiki_read": project_wiki_read,
    }

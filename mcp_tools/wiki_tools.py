"""Local wiki generation tools — materialize and status."""

from __future__ import annotations

import os
from typing import Any

from lintgate.next_action import NextAction, serialize_next_actions
from mcp_tools._disk_helpers import _safe_json, tool_response


def _do_wiki_materialize(
    project_root: str,
    pages: str,
    write: bool,
) -> dict[str, Any]:
    """Core implementation for wiki_materialize."""
    from lintgate.wiki.manifest import load_manifest

    manifest = load_manifest(project_root)
    if manifest is None:
        return {
            "error": "No wiki manifest found at .lintgate/wiki_manifest.yaml",
            "hint": "Create a manifest or check that PyYAML is installed.",
        }

    from lintgate.wiki.composer import compose_all_pages

    theory = _load_theory(project_root)
    compass = _load_compass(project_root)
    composed = compose_all_pages(manifest, project_root, theory, compass)

    if pages:
        requested = {p.strip() for p in pages.split(",")}
        composed = [c for c in composed if c.name in requested]

    wiki_dir = os.path.join(project_root, ".lintgate", "wiki")
    results: list[dict[str, Any]] = []

    if write:
        results = _write_pages(project_root, manifest, composed, wiki_dir)
    else:
        for page in composed:
            results.append(
                {
                    "page": page.name,
                    "pillar": page.pillar,
                    "content_length": len(page.content),
                    "sources": len(page.source_files),
                    "theory_scope": page.theory_scope,
                }
            )

    return {
        "mode": "write" if write else "dry-run",
        "pages_count": len(results),
        "pages": results,
        "wiki_dir": wiki_dir,
    }


def _write_pages(
    project_root: str,
    manifest: Any,
    composed: list[Any],
    wiki_dir: str,
) -> list[dict[str, Any]]:
    """Write composed wiki pages to disk and update freshness state."""
    from lintgate.wiki.freshness import (
        _section_contents_for_page,
        build_page_freshness,
        load_freshness_state,
        save_freshness_state,
    )

    os.makedirs(wiki_dir, exist_ok=True)
    state = load_freshness_state(project_root)
    results: list[dict[str, Any]] = []

    for page in composed:
        out_path = os.path.join(wiki_dir, f"{page.name}.md")
        with open(out_path, "w") as f:
            f.write(page.content)

        section_contents = _section_contents_for_page(page.name, manifest, project_root)
        manifest_page = next((p for p in manifest.pages if p.name == page.name), None)
        m_hash = manifest.manifest_hash_for_page(manifest_page) if manifest_page else ""
        state.pages[page.name] = build_page_freshness(
            page.name, section_contents, m_hash, page.content
        )

        results.append(
            {
                "page": page.name,
                "pillar": page.pillar,
                "written": out_path,
                "content_length": len(page.content),
                "sources": len(page.source_files),
            }
        )

    save_freshness_state(project_root, state)
    return results


def _do_wiki_publish(
    project_root: str,
    out_dir: str,
    check_links: bool,
    site_title: str,
    base_url: str,
) -> dict[str, Any]:
    """Core implementation for wiki_publish_pages."""
    from lintgate.wiki.composer import compose_all_pages
    from lintgate.wiki.manifest import load_manifest
    from lintgate.wiki.pages_publisher import publish_pages

    manifest = load_manifest(project_root)
    if manifest is None:
        return {
            "error": "No wiki manifest found",
            "hint": "Create wiki.yaml or .lintgate/wiki_manifest.yaml first.",
        }

    theory = _load_theory(project_root)
    compass = _load_compass(project_root)
    composed = compose_all_pages(manifest, project_root, theory, compass)

    if not site_title:
        site_title = _detect_site_title(project_root)

    abs_out = os.path.join(project_root, out_dir)
    result = publish_pages(
        manifest=manifest,
        composed=composed,
        project_root=project_root,
        out_dir=abs_out,
        check_links=check_links,
        site_title=site_title,
        base_url=base_url,
    )

    response: dict[str, Any] = {
        "out_dir": abs_out,
        "pages_published": len(result.pages),
        "pages": [{"name": p.name, "slug": p.slug, "size": p.html_size} for p in result.pages],
        "sitemap": result.sitemap_written,
        "link_check": "FAIL" if result.link_errors else "PASS",
    }
    if result.link_errors:
        response["link_errors"] = result.link_errors

    return response


def _detect_site_title(project_root: str) -> str:
    """Auto-detect site title from git remote or directory name."""
    try:
        import subprocess

        proc = subprocess.run(
            ["git", "-C", project_root, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            remote = proc.stdout.strip()
            return remote.rstrip("/").split("/")[-1].replace(".git", "")
    except Exception:
        pass
    return os.path.basename(project_root)


def register(mcp, helpers):
    """Register wiki tools on the shared MCP instance."""

    @mcp.tool()
    def wiki_materialize(path: str, pages: str = "", write: bool = False) -> str:
        """Generate wiki pages locally from manifest.

        Reads ``.lintgate/wiki_manifest.yaml`` and composes wiki pages from
        source markdown sections.  Dry-run by default — shows what would be
        generated with content lengths.  Set ``write=True`` to write pages
        to ``.lintgate/wiki/`` and update freshness state.

        Args:
            path: Project root path.
            pages: Comma-separated page names to generate (empty = all).
            write: If True, write pages to disk. Default is dry-run.
        """
        project_root = helpers["_validate_project_root"](path)
        result = _do_wiki_materialize(project_root, pages, write)

        next_actions = serialize_next_actions(
            [
                NextAction(
                    tool="wiki_status",
                    args={"path": path},
                    reason="Check freshness after materialization"
                    if write
                    else "Preview freshness state",
                    priority=3,
                ),
            ]
        )
        result["next_actions"] = next_actions
        n = result.get("pages_count", 0)
        return tool_response(result, "wiki_materialize", project_root, f"Materialized {n} pages.")

    @mcp.tool()
    def wiki_status(path: str) -> str:
        """Show wiki freshness state — stale, fresh, and missing page counts.

        Reports which section hashes changed per stale page and overall
        freshness score.

        Args:
            path: Project root path.
        """
        project_root = helpers["_validate_project_root"](path)

        from lintgate.wiki.freshness import check_wiki_freshness

        status = check_wiki_freshness(project_root)

        if "error" in status:
            return _safe_json(status, separators=(",", ":"))

        next_actions: list[NextAction] = []
        if status.get("stale", 0) > 0 or status.get("missing", 0) > 0:
            next_actions.append(
                NextAction(
                    tool="wiki_materialize",
                    args={"path": path, "write": True},
                    reason=f"{status['stale']} stale + {status['missing']} missing pages",
                    priority=3,
                    safe=False,
                )
            )

        result: dict[str, Any] = {
            "fresh": status["fresh"],
            "stale": status["stale"],
            "missing": status["missing"],
            "total": status["total"],
            "freshness_score": round(status.get("freshness_score", 0.0), 3),
        }

        page_details: dict[str, Any] = {}
        for name, detail in status.get("pages", {}).items():
            if detail.get("stale", False):
                page_details[name] = detail
        if page_details:
            result["stale_pages"] = page_details

        if next_actions:
            result["next_actions"] = serialize_next_actions(next_actions)

        n = result.get("total", 0)
        return tool_response(result, "wiki_status", project_root, f"Wiki: {n} pages.")

    @mcp.tool()
    def wiki_publish_pages(
        path: str,
        out_dir: str = "_site",
        check_links: bool = True,
        site_title: str = "",
        base_url: str = "",
    ) -> str:
        """Publish wiki pages as a static GitHub Pages site.

        Generates ``page-id/index.html`` per page with sidebar navigation,
        prev/next links within reading rails, dark mode, mobile support,
        sitemap.xml, and robots.txt.

        WHEN TO USE: After ``wiki_materialize`` to produce a deployable site.
        The ``--check-links`` validation (on by default) fails on broken nav.

        Args:
            path: Project root path.
            out_dir: Output directory relative to project root. Default ``_site``.
            check_links: Validate internal links after generation. Default True.
            site_title: Site title for HTML ``<title>``. Auto-detected if empty.
            base_url: Base URL for canonical links and sitemap (e.g. ``https://user.github.io/repo``).
        """
        project_root = helpers["_validate_project_root"](path)
        response = _do_wiki_publish(project_root, out_dir, check_links, site_title, base_url)

        if "error" in response:
            return _safe_json(response, separators=(",", ":"))

        next_actions_list: list[NextAction] = []
        if response.get("link_errors"):
            next_actions_list.append(
                NextAction(
                    tool="wiki_materialize",
                    args={"path": path, "write": True},
                    reason=f"{len(response['link_errors'])} broken links — regenerate pages.",
                    priority=2,
                )
            )
        next_actions_list.append(
            NextAction(
                tool="wiki_status",
                args={"path": path},
                reason="Verify freshness state.",
                priority=4,
            )
        )
        response["next_actions"] = serialize_next_actions(next_actions_list)
        n = response.get("pages_published", 0)
        return tool_response(response, "wiki_publish_pages", project_root, f"Published {n} pages.")

    @mcp.tool()
    def wiki_check_links(path: str) -> str:
        """Check link integrity across all materialized wiki pages.

        Validates that internal page links resolve, detects orphan files
        not in the manifest, and flags manifest pages without materialized
        files.  Also checks that every ``.md`` in ``docs/wiki/`` has a
        corresponding manifest entry.

        WHEN TO USE: After ``wiki_materialize`` or as a lint-shaped health
        check.  The controlplane wiki channel calls this automatically.

        Args:
            path: Project root path.
        """
        project_root = helpers["_validate_project_root"](path)

        from lintgate.wiki.link_checker import (
            check_config_completeness,
            check_wiki_links,
        )

        link_result = check_wiki_links(project_root)
        config_issues = check_config_completeness(project_root)

        response = link_result.to_dict()
        if config_issues:
            response["config_completeness"] = config_issues

        next_actions: list[NextAction] = []
        if not link_result.ok:
            next_actions.append(
                NextAction(
                    tool="wiki_materialize",
                    args={"path": path, "write": True},
                    reason="Fix broken links by regenerating.",
                    priority=2,
                )
            )
        if config_issues:
            next_actions.append(
                NextAction(
                    tool="wiki_materialize",
                    args={"path": path},
                    reason=f"{len(config_issues)} docs/wiki/ files not in manifest.",
                    priority=3,
                )
            )

        if next_actions:
            response["next_actions"] = serialize_next_actions(next_actions)

        n_broken = len(link_result.broken_links) if hasattr(link_result, 'broken_links') else 0
        n_orphans = len(link_result.orphan_files) if hasattr(link_result, 'orphan_files') else 0
        status_str = "PASS" if link_result.ok else f"FAIL ({n_broken} broken, {n_orphans} orphans)"
        return tool_response(response, "wiki_check_links", project_root, f"Link check: {status_str}.")

    return {
        "wiki_materialize": wiki_materialize,
        "wiki_status": wiki_status,
        "wiki_publish_pages": wiki_publish_pages,
        "wiki_check_links": wiki_check_links,
    }


def _load_theory(project_root: str) -> dict[str, Any] | None:
    """Extract live theory profile from source docs."""
    try:
        from lintgate.theory_extractor import extract_theory

        result = extract_theory(project_root)
        return result.get("theory_profile")
    except Exception:
        return None


def _load_compass(project_root: str) -> dict[str, Any] | None:
    """Load compass state from .claude/compass.yaml."""
    try:
        from lintgate.compass_io import load_compass

        state = load_compass(project_root)
        if state is None:
            return None
        return state.to_dict()
    except Exception:
        return None

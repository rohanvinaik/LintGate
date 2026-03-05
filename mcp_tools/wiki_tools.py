"""Local wiki generation tools — materialize and status."""

from __future__ import annotations

import json
import os
from typing import Any

from lintgate.next_action import NextAction, serialize_next_actions


def register(mcp, helpers):
    """Register wiki tools on the shared MCP instance."""

    @mcp.tool()
    def wiki_materialize(
        path: str,
        pages: str = "",
        write: bool = False,
    ) -> str:
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

        from lintgate.wiki.manifest import load_manifest

        manifest = load_manifest(project_root)
        if manifest is None:
            return json.dumps({
                "error": "No wiki manifest found at .lintgate/wiki_manifest.yaml",
                "hint": "Create a manifest or check that PyYAML is installed.",
            })

        from lintgate.wiki.composer import compose_all_pages
        from lintgate.wiki.freshness import (
            build_page_freshness,
            save_freshness_state,
        )

        # Optionally load theory/compass for auto-generated pages
        theory = _load_theory(project_root)
        compass = _load_compass(project_root)

        composed = compose_all_pages(manifest, project_root, theory, compass)

        # Filter to requested pages
        if pages:
            requested = {p.strip() for p in pages.split(",")}
            composed = [c for c in composed if c.name in requested]

        wiki_dir = os.path.join(project_root, ".lintgate", "wiki")
        results: list[dict[str, Any]] = []

        if write:
            os.makedirs(wiki_dir, exist_ok=True)
            from lintgate.wiki.freshness import load_freshness_state

            state = load_freshness_state(project_root)

            for page in composed:
                out_path = os.path.join(wiki_dir, f"{page.name}.md")
                with open(out_path, "w") as f:
                    f.write(page.content)

                # Build freshness state
                from lintgate.wiki.freshness import _section_contents_for_page

                section_contents = _section_contents_for_page(
                    page.name, manifest, project_root
                )
                manifest_page = next(
                    (p for p in manifest.pages if p.name == page.name), None
                )
                m_hash = (
                    manifest.manifest_hash_for_page(manifest_page)
                    if manifest_page
                    else ""
                )
                state.pages[page.name] = build_page_freshness(
                    page.name, section_contents, m_hash, page.content
                )

                results.append({
                    "page": page.name,
                    "pillar": page.pillar,
                    "written": out_path,
                    "content_length": len(page.content),
                    "sources": len(page.source_files),
                })

            save_freshness_state(project_root, state)
        else:
            for page in composed:
                results.append({
                    "page": page.name,
                    "pillar": page.pillar,
                    "content_length": len(page.content),
                    "sources": len(page.source_files),
                    "theory_scope": page.theory_scope,
                })

        next_actions = serialize_next_actions([
            NextAction(
                tool="wiki_status",
                args={"path": path},
                reason="Check freshness after materialization" if write else "Preview freshness state",
                priority=3,
            ),
        ])

        return json.dumps({
            "mode": "write" if write else "dry-run",
            "pages_count": len(results),
            "pages": results,
            "wiki_dir": wiki_dir,
            "next_actions": next_actions,
        }, indent=2)

    @mcp.tool()
    def wiki_status(
        path: str,
    ) -> str:
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
            return json.dumps(status)

        next_actions: list[NextAction] = []
        if status.get("stale", 0) > 0 or status.get("missing", 0) > 0:
            next_actions.append(NextAction(
                tool="wiki_materialize",
                args={"path": path, "write": True},
                reason=f"{status['stale']} stale + {status['missing']} missing pages",
                priority=3,
                safe=False,
            ))

        result = {
            "fresh": status["fresh"],
            "stale": status["stale"],
            "missing": status["missing"],
            "total": status["total"],
            "freshness_score": round(status.get("freshness_score", 0.0), 3),
        }

        # Include per-page details for stale/missing pages only
        page_details: dict[str, Any] = {}
        for name, detail in status.get("pages", {}).items():
            if detail.get("stale", False):
                page_details[name] = detail
        if page_details:
            result["stale_pages"] = page_details

        if next_actions:
            result["next_actions"] = serialize_next_actions(next_actions)

        return json.dumps(result, indent=2)

    return {
        "wiki_materialize": wiki_materialize,
        "wiki_status": wiki_status,
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

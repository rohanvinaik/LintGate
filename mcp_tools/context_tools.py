"""Context tools — context_guidance, audit_context_health, bootstrap_context_files,
context_patch_review, context_patch_apply, extract_theory_constraints,
extract_project_theory, build_theory_pack, get_theory_context."""

from __future__ import annotations

import json


def _do_patch_review(project_root: str) -> dict:
    """Core implementation for context_patch_review."""
    from lintgate.context_bootstrap import (
        ContextPatch,
        apply_context_patch,
        generate_context_patch,
    )
    from lintgate.controlplane.session_memory import get_or_create_session

    session = get_or_create_session(project_root)
    pending = [p for p in session.pending_patches if p.get("status", "pending") == "pending"]

    if not pending:
        return {"pending_count": 0, "message": "No pending context patches."}

    previews = []
    for p_dict in pending:
        patch = ContextPatch.from_dict(p_dict)
        refreshed = generate_context_patch(
            project_root, trigger=patch.trigger, evidence=patch.evidence
        )
        if refreshed is None:
            previews.append(
                {
                    "patch_id": patch.patch_id,
                    "section_id": patch.section_id,
                    "trigger": patch.trigger,
                    "rationale": patch.rationale,
                    "diff_preview": None,
                    "status": "no_op",
                }
            )
            continue

        refreshed.patch_id = patch.patch_id
        preview = apply_context_patch(project_root, refreshed, dry_run=True)
        previews.append(
            {
                "patch_id": refreshed.patch_id,
                "section_id": refreshed.section_id,
                "trigger": refreshed.trigger,
                "rationale": refreshed.rationale,
                "diff_preview": preview.get("diff_preview"),
                "status": "pending",
            }
        )

    return {"pending_count": len(pending), "patches": previews}


def _do_patch_apply(
    project_root: str,
    patch_ids: list[str] | None,
    dry_run: bool,
) -> dict:
    """Core implementation for context_patch_apply."""
    from lintgate.context_bootstrap import (
        ContextPatch,
        apply_context_patch,
        generate_context_patch,
    )
    from lintgate.controlplane.session_memory import (
        get_or_create_session,
        save_session,
    )

    session = get_or_create_session(project_root)
    pending = [p for p in session.pending_patches if p.get("status", "pending") == "pending"]

    if patch_ids is not None:
        pending = [p for p in pending if p.get("patch_id") in patch_ids]

    if not pending:
        return {"applied": 0, "message": "No matching pending patches."}

    results = []
    for p_dict in pending:
        patch = ContextPatch.from_dict(p_dict)
        refreshed = generate_context_patch(
            project_root, trigger=patch.trigger, evidence=patch.evidence
        )
        if refreshed is None:
            results.append(
                {
                    "patch_id": patch.patch_id,
                    "section_id": patch.section_id,
                    "applied": False,
                    "status": "no_op",
                    "diff_preview": None,
                }
            )
            if not dry_run:
                p_dict["status"] = "applied"
            continue

        refreshed.patch_id = patch.patch_id
        result = apply_context_patch(project_root, refreshed, dry_run=dry_run)
        results.append(
            {
                "patch_id": refreshed.patch_id,
                "section_id": refreshed.section_id,
                "applied": result.get("applied", False),
                "status": "applied" if result.get("applied", False) else "pending",
                "diff_preview": result.get("diff_preview"),
            }
        )
        if result.get("applied") and not dry_run:
            p_dict["status"] = "applied"

    if not dry_run:
        save_session(session)
        import contextlib

        with contextlib.suppress(Exception):
            from lintgate.state import log_feature_usage

            log_feature_usage("living_context", project_root)

    return {
        "dry_run": dry_run,
        "applied": sum(1 for r in results if r["applied"]),
        "results": results,
    }


def _do_bootstrap(project_root: str, **kwargs) -> dict:
    """Core implementation for bootstrap_context_files."""
    import contextlib

    from lintgate.context_bootstrap import bootstrap_context_files as _bootstrap

    with contextlib.suppress(Exception):
        from lintgate.state import log_feature_usage

        log_feature_usage("bootstrap", project_root)

    return _bootstrap(project_root, **kwargs)


def _do_extract_theory(project_root: str) -> dict:
    """Core implementation for extract_project_theory."""
    import contextlib

    from lintgate.theory_extractor import extract_theory

    with contextlib.suppress(Exception):
        from lintgate.state import log_feature_usage

        log_feature_usage("theory_extraction", project_root)

    return extract_theory(project_root)


def _do_build_theory_pack(project_root: str, include_full_profile: bool = False) -> dict:
    """Core implementation for build_theory_pack."""
    import contextlib

    from lintgate.theory_extractor import build_theory_pack as _build

    with contextlib.suppress(Exception):
        from lintgate.state import log_feature_usage

        log_feature_usage("theory_extraction", project_root)

    return _build(project_root, include_full_profile=include_full_profile)


def register(mcp, helpers):
    """Register context tools on the shared MCP instance."""

    @mcp.tool()
    def context_guidance(path: str, files: list[str] | None = None) -> str:
        """Summarize context guidance and machine-usable rules for a project."""
        from lintgate.context_guidance import build_context_guidance, summarize_context_guidance

        project_root = helpers["_validate_project_root"](path)
        guidance = build_context_guidance(project_root, files=files)
        guidance["summary"] = summarize_context_guidance(guidance)
        return json.dumps(guidance, indent=2)

    @mcp.tool()
    def audit_context_health(path: str) -> str:
        """Audit CLAUDE.md/AGENTS.md quality against LLM context file best practices.

        Checks: length (configurable), structure, staleness, contradictions,
        machine-rule coverage, and path reference validation.

        Configure thresholds in lintgate.yaml under linters.context_auditor.
        """
        from lintgate.context_auditor import audit_context_health as _audit

        return json.dumps(_audit(helpers["_validate_project_root"](path)), indent=2)

    @mcp.tool()
    def bootstrap_context_files(
        path: str,
        write: bool = False,
        overwrite: bool = False,
        include_theory_rules_doc: bool = True,
        max_machine_rules: int = 12,
        model_id: str | None = None,
        use_model_profile: bool = True,
    ) -> str:
        """Generate project-specific CLAUDE.md and AGENTS.md from documented principles.

        WHEN TO USE: On first session with a project, or when project documentation
        changes significantly. Scans all markdown docs in the repo to extract principles,
        anti-patterns, and lint rules, then generates context files that persist across sessions.

        Example: bootstrap_context_files(path="/my/project", write=True)

        Returns ``needs_review`` — items where automated analysis was uncertain.
        Returns ``quick_wins`` — concrete next steps.
        Returns ``agent_instructions`` — ordered workflow for what to do with the result.
        """
        return json.dumps(
            _do_bootstrap(
                helpers["_validate_project_root"](path),
                write=write,
                overwrite=overwrite,
                include_theory_rules_doc=include_theory_rules_doc,
                max_machine_rules=max_machine_rules,
                model_id=model_id,
                use_model_profile=use_model_profile,
            ),
            indent=2,
        )

    @mcp.tool()
    def context_patch_review(path: str) -> str:
        """Review pending updates to CLAUDE.md auto-managed sections.

        Shows pending patches with diff previews. Use context_patch_apply
        to write the changes after reviewing.

        Args:
            path: Project root path.
        """
        result = _do_patch_review(helpers["_validate_project_root"](path))
        if result.get("pending_count", 0) > 0:
            result["next_actions"] = [
                {
                    "tool": "context_patch_apply",
                    "reason": "Apply pending patches",
                    "args": {"path": path},
                }
            ]
        return json.dumps(result, indent=2)

    @mcp.tool()
    def context_patch_apply(
        path: str, patch_ids: list[str] | None = None, dry_run: bool = False
    ) -> str:
        """Apply pending context patches to CLAUDE.md managed sections.

        By default applies all pending patches. Pass patch_ids to apply specific ones.

        Args:
            path: Project root path.
            patch_ids: Specific patch IDs to apply. If None, applies all pending.
            dry_run: Preview changes without writing (default False).
        """
        return json.dumps(
            _do_patch_apply(helpers["_validate_project_root"](path), patch_ids, dry_run), indent=2
        )

    @mcp.tool()
    def extract_theory_constraints(path: str) -> str:
        """Extract enforceable lint rules from CLAUDE.md/AGENTS.md prose directives."""
        from lintgate.theory_extractor import extract_theory

        return json.dumps(
            extract_theory(helpers["_validate_project_root"](path))["enforceable_rules"], indent=2
        )

    @mcp.tool()
    def extract_project_theory(path: str) -> str:
        """Extract documented principles, philosophy, and patterns from project markdown files.

        WHEN TO USE: To understand a project's documented guidelines before making changes,
        or to check if changes align with documented principles.
        """
        return json.dumps(_do_extract_theory(helpers["_validate_project_root"](path)), indent=2)

    @mcp.tool()
    def build_theory_pack(path: str, include_full_profile: bool = False) -> str:
        """Build a compact summary of project principles for quick reference.

        Use this instead of extract_project_theory when you need a token-efficient
        overview for ongoing work.
        """
        return json.dumps(
            _do_build_theory_pack(helpers["_validate_project_root"](path), include_full_profile),
            indent=2,
        )

    @mcp.tool()
    def get_theory_context(
        path: str, facet: str | None = None, keywords: list[str] | None = None, max_claims: int = 5
    ) -> str:
        """Look up specific documented project principles by topic or keywords.

        Args:
            path: Project root path.
            facet: Optional category filter (core_theory, problem_solving, alignment, architecture, anti_patterns, abstractions).
            keywords: Optional keywords to match against principle text.
            max_claims: Max principles to return (default 5). Must be > 0.
        """
        if max_claims <= 0:
            raise ValueError("max_claims must be > 0")
        from lintgate.theory_extractor import get_theory_context as _get

        return json.dumps(
            _get(
                helpers["_validate_project_root"](path),
                facet=facet,
                keywords=keywords,
                max_claims=max_claims,
            ),
            indent=2,
        )

    return {
        "context_guidance": context_guidance,
        "audit_context_health": audit_context_health,
        "bootstrap_context_files": bootstrap_context_files,
        "context_patch_review": context_patch_review,
        "context_patch_apply": context_patch_apply,
        "extract_theory_constraints": extract_theory_constraints,
        "extract_project_theory": extract_project_theory,
        "build_theory_pack": build_theory_pack,
        "get_theory_context": get_theory_context,
    }

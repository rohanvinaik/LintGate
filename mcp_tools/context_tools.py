"""Context tools — context_guidance, audit_context_health, bootstrap_context_files,
context_patch_review, context_patch_apply, extract_theory_constraints,
extract_project_theory, build_theory_pack, get_theory_context."""

from __future__ import annotations

import json


def register(mcp, helpers):
    """Register context tools on the shared MCP instance."""

    @mcp.tool()
    def context_guidance(
        path: str,
        files: list[str] | None = None,
    ) -> str:
        """Summarize context guidance and machine-usable rules for a project."""
        from lintgate.context_guidance import (
            build_context_guidance,
            summarize_context_guidance,
        )

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

        Generates:
        - CLAUDE.md — project principles, anti-patterns, and enforceable lint rules
        - AGENTS.md — tool reference for all agents
        - .claude/rules/theory.md (optional) — extracted principles as rules

        Default mode is non-destructive (`write=false`) — returns drafts for review.
        Set `write=true` to create/update files on disk.

        Returns `needs_review` — items where automated analysis was uncertain and the
        agent can cheaply resolve. Returns `quick_wins` — concrete next steps.
        Returns `agent_instructions` — ordered workflow for what to do with the result.
        """
        from lintgate.context_bootstrap import (
            bootstrap_context_files as _bootstrap_context_files,
        )

        project_root = helpers["_validate_project_root"](path)

        # Telemetry: track bootstrap usage
        import contextlib

        with contextlib.suppress(Exception):
            from lintgate.state import log_feature_usage

            log_feature_usage("bootstrap", project_root)

        return json.dumps(
            _bootstrap_context_files(
                project_root,
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
        from lintgate.context_bootstrap import (
            ContextPatch,
            apply_context_patch,
            generate_context_patch,
        )
        from lintgate.controlplane.session_memory import get_or_create_session

        project_root = helpers["_validate_project_root"](path)
        session = get_or_create_session(project_root)

        pending = [
            p
            for p in session.pending_patches
            if p.get("status", "pending") == "pending"
        ]

        if not pending:
            return json.dumps(
                {"pending_count": 0, "message": "No pending context patches."}, indent=2
            )

        previews = []
        for p_dict in pending:
            patch = ContextPatch.from_dict(p_dict)
            # Rebuild patch from current file state so preview reflects cumulative changes.
            refreshed = generate_context_patch(
                project_root,
                trigger=patch.trigger,
                evidence=patch.evidence,
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

            # Preserve original patch id for stable review/apply UX.
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

        return json.dumps(
            {
                "pending_count": len(pending),
                "patches": previews,
                "next_actions": [
                    {
                        "tool": "context_patch_apply",
                        "reason": "Apply pending context patches explicitly",
                        "args": {"path": path},
                    }
                ],
            },
            indent=2,
        )

    @mcp.tool()
    def context_patch_apply(
        path: str,
        patch_ids: list[str] | None = None,
        dry_run: bool = False,
    ) -> str:
        """Apply pending context patches to CLAUDE.md managed sections.

        By default applies all pending patches. Pass patch_ids to apply specific ones.

        Args:
            path: Project root path.
            patch_ids: Specific patch IDs to apply. If None, applies all pending.
            dry_run: Preview changes without writing (default False).
        """
        from lintgate.context_bootstrap import (
            ContextPatch,
            apply_context_patch,
            generate_context_patch,
        )
        from lintgate.controlplane.session_memory import (
            get_or_create_session,
            save_session,
        )

        project_root = helpers["_validate_project_root"](path)
        session = get_or_create_session(project_root)

        pending = [
            p
            for p in session.pending_patches
            if p.get("status", "pending") == "pending"
        ]

        if patch_ids is not None:
            pending = [p for p in pending if p.get("patch_id") in patch_ids]

        if not pending:
            return json.dumps(
                {"applied": 0, "message": "No matching pending patches."}, indent=2
            )

        results = []
        for p_dict in pending:
            patch = ContextPatch.from_dict(p_dict)

            # Rebuild patch from latest on-disk CLAUDE.md before applying.
            # This prevents stale patch.new_content from clobbering earlier
            # patches when multiple pending patches target the same section.
            refreshed = generate_context_patch(
                project_root,
                trigger=patch.trigger,
                evidence=patch.evidence,
            )
            if refreshed is None:
                # No-op means already reflected or not applicable anymore.
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
                # Mark patch as applied in session
                p_dict["status"] = "applied"

        if not dry_run:
            save_session(session)

            # Telemetry: track living_context usage (only for real applies)
            import contextlib

            with contextlib.suppress(Exception):
                from lintgate.state import log_feature_usage

                log_feature_usage("living_context", project_root)

        return json.dumps(
            {
                "dry_run": dry_run,
                "applied": sum(1 for r in results if r["applied"]),
                "results": results,
            },
            indent=2,
        )

    @mcp.tool()
    def extract_theory_constraints(path: str) -> str:
        """Extract enforceable lint rules from CLAUDE.md/AGENTS.md prose directives.

        Analyzes DO NOT / MUST directives and proposes LINTGATE_FORBID_REGEX /
        LINTGATE_REQUIRE_REGEX rules. Deduplicates against existing rules.
        Returns proposed rules with copy-paste-ready lines for CLAUDE.md.
        """
        from lintgate.theory_extractor import extract_theory

        result = extract_theory(helpers["_validate_project_root"](path))
        # Return just the enforceable rules for backward compat
        return json.dumps(result["enforceable_rules"], indent=2)

    @mcp.tool()
    def extract_project_theory(path: str) -> str:
        """Extract documented principles, philosophy, and patterns from project markdown files.

        WHEN TO USE: To understand a project's documented guidelines before making changes,
        or to check if changes align with documented principles.

        Scans all markdown documents in the codebase to identify: core principles,
        problem-solving approach, alignment criteria, architectural philosophy,
        anti-patterns, and key abstractions. Returns a structured profile with 6
        categories, each containing claims extracted from documentation with source
        references. Also includes enforceable lint rules as a subset.
        """
        from lintgate.theory_extractor import extract_theory

        project_root = helpers["_validate_project_root"](path)

        # Telemetry: track theory_extraction usage
        import contextlib

        with contextlib.suppress(Exception):
            from lintgate.state import log_feature_usage

            log_feature_usage("theory_extraction", project_root)

        return json.dumps(extract_theory(project_root), indent=2)

    @mcp.tool()
    def build_theory_pack(
        path: str,
        include_full_profile: bool = False,
    ) -> str:
        """Build a compact summary of project principles for quick reference.

        Returns a two-level payload:
        - Summary (~500-1500 tokens): enforceable rules, principle summaries, anti-pattern list.
        - Full detail: complete documented claims for deeper lookup
          (only included when include_full_profile=true).

        Use this instead of extract_project_theory when you need a token-efficient
        overview for ongoing work.
        """
        from lintgate.theory_extractor import build_theory_pack as _build

        project_root = helpers["_validate_project_root"](path)

        # Telemetry: track theory_extraction usage
        import contextlib

        with contextlib.suppress(Exception):
            from lintgate.state import log_feature_usage

            log_feature_usage("theory_extraction", project_root)

        return json.dumps(
            _build(
                project_root,
                include_full_profile=include_full_profile,
            ),
            indent=2,
        )

    @mcp.tool()
    def get_theory_context(
        path: str,
        facet: str | None = None,
        keywords: list[str] | None = None,
        max_claims: int = 5,
    ) -> str:
        """Look up specific documented project principles by topic or keywords.

        WHEN TO USE: When you need deeper reasoning about a specific issue or design
        decision. Returns the most relevant documented principles matched by
        category and/or keyword overlap.

        Args:
            path: Project root path.
            facet: Optional category filter (core_theory, problem_solving,
                alignment, architecture, anti_patterns, abstractions).
            keywords: Optional keywords to match against principle text.
            max_claims: Max principles to return (default 5).
                Must be > 0.
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

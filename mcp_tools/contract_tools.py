"""MCP tools for channel contract auditing.

Exposes schema validation (wiring, sheaf condition, schema size) as an
interactive MCP tool for on-demand contract health checks.
"""

from __future__ import annotations


def register(mcp, helpers):
    """Register contract audit tools on the MCP instance."""

    @mcp.tool()
    async def contract_audit(
        path: str = ".",
        channels: str = "",
    ) -> str:
        """Audit channel metric contract health.

        Checks wiring (WIRE001), result completeness (WIRE002),
        sheaf condition (WIRE003), and schema growth (WIRE004).

        Args:
            path: Project root path.
            channels: Comma-separated channel names (default: all).
        """
        from lintgate.controlplane.metric_schema import (
            check_schema_size,
            check_sheaf_condition,
            clear_schemas,
            get_all_schemas,
            register_all_schemas,
            validate_wiring,
        )

        clear_schemas()
        register_all_schemas()

        # Determine active channels
        if channels:
            active = [c.strip() for c in channels.split(",")]
        else:
            active = list(get_all_schemas().keys())

        # Run all checks
        wiring_issues = validate_wiring(active)
        sheaf_issues = check_sheaf_condition(active)
        size_issues = check_schema_size()

        # Build report
        all_issues = []
        for issue in wiring_issues:
            code = "WIRE001" if issue.issue_type == "missing_publisher" else "WIRE002"
            all_issues.append({
                "code": code,
                "consumer": issue.consumer,
                "key": issue.key,
                "detail": issue.missing_publisher,
            })
        for issue in sheaf_issues:
            all_issues.append({
                "code": "WIRE003",
                "consumer": issue.consumer,
                "key": issue.key,
                "detail": issue.missing_publisher,
            })
        for issue in size_issues:
            all_issues.append({
                "code": "WIRE004",
                "consumer": issue.consumer,
                "key": issue.key,
                "detail": issue.missing_publisher,
            })

        # Schema summary
        schemas = get_all_schemas()
        schema_summary = {}
        for name, schema in schemas.items():
            schema_summary[name] = {
                "publishes": len(schema.publishes),
                "consumes": len(schema.consumes),
                "finding_deps": len(schema.finding_deps),
            }

        result = {
            "status": "pass" if not all_issues else "fail",
            "active_channels": active,
            "issues": all_issues,
            "issue_count": len(all_issues),
            "schema_summary": schema_summary,
            "next_actions": [],
        }

        if all_issues:
            result["next_actions"].append({
                "tool": "controlplane_run",
                "args": {"path": path},
                "reason": f"{len(all_issues)} contract issue(s) found — run full analysis",
            })

        clear_schemas()
        return helpers["_json_dumps"](result, output_mode="compact")

    return {"contract_audit": contract_audit}

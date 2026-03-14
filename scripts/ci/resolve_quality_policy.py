from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    coverage_min = 80
    diff_coverage_min = 80
    source_packages = ["lintgate", "mcp_tools"]

    cfg = Path(".claude/lintgate.yaml")
    if cfg.exists():
        try:
            import yaml

            raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            qp = raw.get("quality_policy", {})
            cov = qp.get("coverage", {})
            coverage_min = int(cov.get("global_threshold", coverage_min))
            diff_coverage_min = int(cov.get("diff_threshold", diff_coverage_min))
            source_packages = cov.get("source_packages", source_packages)
            if not isinstance(source_packages, list):
                source_packages = ["lintgate", "mcp_tools"]
            source_packages = [str(p) for p in source_packages if str(p).strip()]
            if not source_packages:
                source_packages = ["lintgate", "mcp_tools"]
        except Exception:
            pass

    print(
        json.dumps(
            {
                "coverage_min": coverage_min,
                "diff_coverage_min": diff_coverage_min,
                "source_packages": source_packages,
                "cov_args": [f"--cov={pkg}" for pkg in source_packages],
            }
        )
    )


if __name__ == "__main__":
    main()

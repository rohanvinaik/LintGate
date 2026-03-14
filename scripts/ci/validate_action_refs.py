from __future__ import annotations

import os
import re
from pathlib import Path
from urllib import error, request


def main() -> None:
    pattern = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)")
    workflow_files = sorted(Path(".github/workflows").glob("*.yml"))
    references: dict[str, list[str]] = {}
    for workflow in workflow_files:
        for line in workflow.read_text(encoding="utf-8").splitlines():
            match = pattern.match(line)
            if not match:
                continue
            ref = match.group(1).strip().strip("\"'")
            references.setdefault(ref, []).append(str(workflow))

    checked_repos: dict[str, tuple[bool, str]] = {}
    failures: list[str] = []
    for ref, files in sorted(references.items()):
        if ref.startswith("./") or ref.startswith("docker://"):
            continue
        if "@" not in ref:
            failures.append(f"{ref}: missing @ref (in {', '.join(files)})")
            continue

        action_path, _ = ref.split("@", 1)
        segments = action_path.split("/")
        if len(segments) < 2:
            failures.append(f"{ref}: expected owner/repo@ref (in {', '.join(files)})")
            continue
        repo = "/".join(segments[:2])

        if repo not in checked_repos:
            headers = {"User-Agent": "lintgate-action-ref-check"}
            token = os.getenv("GITHUB_TOKEN", "").strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"
            req = request.Request(f"https://api.github.com/repos/{repo}", headers=headers)
            try:
                with request.urlopen(req, timeout=15) as resp:  # nosec B310 — URL is hardcoded to https://api.github.com
                    checked_repos[repo] = (200 <= resp.status < 300, f"HTTP {resp.status}")
            except error.HTTPError as exc:
                checked_repos[repo] = (False, f"HTTP {exc.code}")
            except Exception as exc:
                checked_repos[repo] = (False, f"{type(exc).__name__}: {exc}")

        ok, detail = checked_repos[repo]
        if not ok:
            failures.append(f"{ref}: repo check failed ({detail}) in {', '.join(files)}")

    if failures:
        print("Invalid/unresolvable GitHub Action references detected:")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)

    print(f"Validated {len(references)} action refs across {len(workflow_files)} workflows.")


if __name__ == "__main__":
    main()

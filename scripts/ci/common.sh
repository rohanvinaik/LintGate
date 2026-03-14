#!/usr/bin/env bash

lintgate_repo_root() {
  git rev-parse --show-toplevel 2>/dev/null || pwd
}

lintgate_cd_repo_root() {
  local root
  root="$(lintgate_repo_root)"
  cd "$root" || exit 1
  printf '%s\n' "$root"
}

lintgate_source_local_env() {
  if [ -z "${GITHUB_ACTIONS:-}" ] && [ -f ".env" ]; then
    # shellcheck disable=SC1091
    . ".env"
  fi
}

lintgate_current_branch() {
  git rev-parse --abbrev-ref HEAD 2>/dev/null || echo ""
}

lintgate_resolve_python() {
  local version="$1"
  local named="python$version"
  if command -v "$named" >/dev/null 2>&1; then
    printf '%s\n' "$named"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    local active
    active="$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
    if [ "$active" = "$version" ]; then
      printf '%s\n' python
      return 0
    fi
  fi
  return 1
}

lintgate_test_dir() {
  if [ -d tests ]; then
    printf 'tests\n'
  elif [ -d test ]; then
    printf 'test\n'
  else
    printf '.\n'
  fi
}

lintgate_project_key_from_properties() {
  python - <<'PY'
from pathlib import Path

path = Path("sonar-project.properties")
if not path.exists():
    raise SystemExit(0)
for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
    if line.startswith("sonar.projectKey="):
        print(line.split("=", 1)[1].strip())
        break
PY
}

lintgate_current_pr_context() {
  if [ -n "${GITHUB_EVENT_NAME:-}" ] && [ -f "${GITHUB_EVENT_PATH:-}" ]; then
    python - <<'PY'
import json
import os
from pathlib import Path

event_name = os.getenv("GITHUB_EVENT_NAME", "")
event_path = Path(os.getenv("GITHUB_EVENT_PATH", ""))
if not event_path.exists():
    raise SystemExit(0)
data = json.loads(event_path.read_text(encoding="utf-8"))

if event_name == "pull_request":
    pr = data.get("pull_request") or {}
    num = pr.get("number")
    base = ((pr.get("base") or {}).get("ref")) or os.getenv("GITHUB_BASE_REF", "")
    head = ((pr.get("head") or {}).get("ref")) or os.getenv("GITHUB_HEAD_REF", "")
    if num and base and head:
        print(f"{num}\t{base}\t{head}")
    raise SystemExit(0)

if event_name == "workflow_run":
    wr = data.get("workflow_run") or {}
    prs = wr.get("pull_requests") or []
    if prs:
        pr = prs[0]
        num = pr.get("number")
        base = ((pr.get("base") or {}).get("ref")) or "main"
        head = wr.get("head_branch") or ""
        if num and base and head:
            print(f"{num}\t{base}\t{head}")
    raise SystemExit(0)
PY
    return 0
  fi

  if ! command -v gh >/dev/null 2>&1; then
    return 1
  fi
  local branch
  branch="$(lintgate_current_branch)"
  if [ -z "$branch" ]; then
    return 1
  fi
  gh pr list --head "$branch" --state open --json number,baseRefName,headRefName --limit 1 2>/dev/null \
    | python -c 'import json,sys
prs=json.load(sys.stdin)
if prs:
    pr=prs[0]
    print(f"{pr.get(\"number\",\"\")}\t{pr.get(\"baseRefName\",\"\")}\t{pr.get(\"headRefName\",\"\")}")' 2>/dev/null
}

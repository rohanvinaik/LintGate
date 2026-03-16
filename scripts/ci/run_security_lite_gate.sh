#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
. "$ROOT/scripts/ci/common.sh"
lintgate_source_local_env

if ! command -v gitleaks >/dev/null 2>&1; then
  echo "gitleaks is not installed" >&2
  exit 1
fi

PYBIN="$(lintgate_resolve_python "3.11")" || {
  echo "python3.11 not found" >&2
  exit 1
}
if ! "$PYBIN" -c 'import bandit, pip_audit' >/dev/null 2>&1; then
  echo "python3.11 is missing bandit/pip-audit" >&2
  exit 1
fi

GITLEAKS_CONFIG=.gitleaks.toml gitleaks git . --no-banner --redact

"$PYBIN" -m bandit -q -r . \
  -x tests,.venv,venv,env,__pycache__,.git,node_modules,docs \
  -s B101,B105,B106,B108,B110,B112,B310,B311,B404,B603,B605,B607

shopt -s nullglob
REQS=(requirements*.txt)
if [ ${#REQS[@]} -gt 0 ]; then
  for req in "${REQS[@]}"; do
    "$PYBIN" -m pip_audit -r "$req"
  done
fi
shopt -u nullglob

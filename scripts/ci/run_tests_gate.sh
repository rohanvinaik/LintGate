#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
. "$ROOT/scripts/ci/common.sh"
lintgate_source_local_env

VERSION=""
while [ $# -gt 0 ]; do
  case "$1" in
    --python-version)
      VERSION="$2"
      shift 2
      ;;
    --python-version=*)
      VERSION="${1#--python-version=}"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [ -z "$VERSION" ]; then
  echo "--python-version is required" >&2
  exit 2
fi

PYBIN="$(lintgate_resolve_python "$VERSION")" || {
  echo "python$VERSION not found" >&2
  exit 1
}
if ! "$PYBIN" -c 'import pytest, pytest_cov' >/dev/null 2>&1; then
  echo "python$VERSION is missing pytest/pytest-cov" >&2
  exit 1
fi

"$PYBIN" scripts/ci/validate_action_refs.py

POLICY_JSON="$("$PYBIN" scripts/ci/resolve_quality_policy.py)"
readarray -t POLICY_LINES < <(
  "$PYBIN" -c 'import json,sys
p=json.load(sys.stdin)
print(p["coverage_min"])
print(p["diff_coverage_min"])
print(" ".join(p["cov_args"]))' <<<"$POLICY_JSON"
)
COVERAGE_MIN="${POLICY_LINES[0]}"
DIFF_COVERAGE_MIN="${POLICY_LINES[1]}"
read -r -a COV_ARGS <<<"${POLICY_LINES[2]}"

TEST_DIR="$(lintgate_test_dir)"
SUFFIX=""
if [ "${LINTGATE_VERSIONED_ARTIFACTS:-0}" = "1" ]; then
  SUFFIX="-$VERSION"
fi
COVERAGE_XML="coverage${SUFFIX}.xml"
COVERAGE_JSON="coverage${SUFFIX}.json"
JUNIT_XML="pytest-results${SUFFIX}.xml"
COVERAGE_DB=".coverage${SUFFIX}"
rm -f "$COVERAGE_DB"

echo "Coverage telemetry target: ${COVERAGE_MIN}%"
echo "Coverage packages: ${COV_ARGS[*]}"

COVERAGE_FILE="$COVERAGE_DB" "$PYBIN" -m pytest "$TEST_DIR" \
  "${COV_ARGS[@]}" \
  --cov-config=.coveragerc \
  --cov-report="xml:$COVERAGE_XML" --cov-report="json:$COVERAGE_JSON" --cov-report=term-missing \
  --junitxml="$JUNIT_XML" \
  --tb=short -q

if [ "${LINTGATE_VERSIONED_ARTIFACTS:-0}" = "1" ] && [ "$VERSION" = "3.12" ]; then
  cp "$COVERAGE_XML" coverage.xml
  cp "$COVERAGE_JSON" coverage.json
  cp "$JUNIT_XML" pytest-results.xml
fi

HEAD="${GITHUB_SHA:-HEAD}"
BASE=""
EVENT_NAME="${GITHUB_EVENT_NAME:-}"
if [ "$EVENT_NAME" = "pull_request" ] && [ -n "${GITHUB_BASE_REF:-}" ]; then
  git fetch --no-tags --depth=1 origin "$GITHUB_BASE_REF" >/dev/null 2>&1 || true
  BASE="origin/$GITHUB_BASE_REF"
elif [ "$EVENT_NAME" = "push" ]; then
  BASE="HEAD~1"
else
  PR_CONTEXT="$(lintgate_current_pr_context || true)"
  if [ -n "$PR_CONTEXT" ]; then
    PR_BASE="$(printf '%s' "$PR_CONTEXT" | cut -f2)"
    if [ -n "$PR_BASE" ]; then
      git fetch --no-tags --depth=1 origin "$PR_BASE" >/dev/null 2>&1 || true
      BASE="origin/$PR_BASE"
    fi
  elif [ "$(lintgate_current_branch)" = "main" ]; then
    BASE="HEAD~1"
  elif git rev-parse --verify origin/main >/dev/null 2>&1; then
    BASE="origin/main"
  else
    BASE="HEAD~1"
  fi
fi

if [ -n "$BASE" ] && ! git rev-parse --verify "$BASE" >/dev/null 2>&1; then
  BASE=""
fi

set +e
if [ -n "$BASE" ]; then
  python -m lintgate.symbol_gate_runner \
    --project-root "$ROOT" \
    --coverage-json "$COVERAGE_JSON" \
    --base "$BASE" \
    --head "$HEAD" \
    --surface ci
  GATE_STATUS=$?
else
  python -m lintgate.symbol_gate_runner \
    --project-root "$ROOT" \
    --coverage-json "$COVERAGE_JSON" \
    --head "$HEAD" \
    --surface ci
  GATE_STATUS=$?
fi
set -e

REF_NAME="${GITHUB_REF_NAME:-$(lintgate_current_branch)}"
ADVISORY_MODE="$(
  EVENT_NAME="$EVENT_NAME" REF_NAME="$REF_NAME" python - <<'PY' 2>/dev/null || echo 0
import fnmatch
import os

import yaml

try:
    c = yaml.safe_load(open("gate_contract.yaml", encoding="utf-8"))
    policy = c.get("symbol_gate", {}).get("advisory_policy", {})
    event = os.getenv("EVENT_NAME", "local")
    ref = os.getenv("REF_NAME", "")
    if event == "pull_request" and policy.get("pr_always_advisory", True):
        print(1)
    else:
        patterns = policy.get("advisory_branches", ["main"])
        print(1 if any(fnmatch.fnmatch(ref, p) for p in patterns) else 0)
except Exception:
    print(0)
PY
)"

if [ "$GATE_STATUS" -ne 0 ] && [ "$ADVISORY_MODE" -eq 1 ]; then
  echo "Symbol coverage gate failed; advisory for ${EVENT_NAME:-local} on ${REF_NAME:-unknown}."
elif [ "$GATE_STATUS" -ne 0 ]; then
  exit "$GATE_STATUS"
fi

if [ "$EVENT_NAME" = "pull_request" ] && command -v diff-cover >/dev/null 2>&1 && [ -n "${GITHUB_BASE_REF:-}" ]; then
  git fetch --no-tags --depth=1 origin "$GITHUB_BASE_REF" >/dev/null 2>&1 || true
  diff-cover "$COVERAGE_XML" \
    --compare-branch="origin/$GITHUB_BASE_REF" \
    --fail-under="$DIFF_COVERAGE_MIN" || true
fi

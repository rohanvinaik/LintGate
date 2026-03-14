#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
. "$ROOT/scripts/ci/common.sh"
lintgate_source_local_env

REQUIRE_TOKEN="${LINTGATE_REQUIRE_SONAR_TOKEN:-0}"
PROJECT_KEY="${SONAR_PROJECT_KEY:-$(lintgate_project_key_from_properties)}"
SONAR_HOST="${SONAR_HOST_URL:-https://sonarcloud.io}"

if [ -z "${SONAR_TOKEN:-}" ] || [ -z "$PROJECT_KEY" ]; then
  if [ "$REQUIRE_TOKEN" = "1" ] || [ -z "${GITHUB_ACTIONS:-}" ]; then
    echo "SONAR_TOKEN or sonar.projectKey is missing" >&2
    exit 1
  fi
  echo "SONAR_TOKEN not configured; skipping SonarQube Cloud scan."
  exit 0
fi

if [ ! -f coverage.xml ]; then
  PYBIN="$(lintgate_resolve_python "3.12")" || {
    echo "python3.12 not found" >&2
    exit 1
  }
  if ! "$PYBIN" -c 'import pytest, pytest_cov' >/dev/null 2>&1; then
    echo "python3.12 is missing pytest/pytest-cov" >&2
    exit 1
  fi
  TEST_DIR="$(lintgate_test_dir)"
  "$PYBIN" -m pytest "$TEST_DIR" \
    --cov=lintgate --cov=mcp_tools --cov-config=.coveragerc \
    --cov-report=xml:coverage.xml --cov-report=term:skip-covered \
    --tb=short -q
fi

SCANNER_PATH=""
if command -v pysonar-scanner >/dev/null 2>&1; then
  SCANNER_PATH="pysonar-scanner"
elif command -v sonar-scanner >/dev/null 2>&1; then
  SCANNER_PATH="sonar-scanner"
else
  echo "pysonar-scanner or sonar-scanner not found" >&2
  exit 1
fi

PR_CONTEXT="$(lintgate_current_pr_context || true)"
PR_NUMBER=""
PR_BASE=""
PR_BRANCH=""
if [ -n "$PR_CONTEXT" ]; then
  PR_NUMBER="$(printf '%s' "$PR_CONTEXT" | cut -f1)"
  PR_BASE="$(printf '%s' "$PR_CONTEXT" | cut -f2)"
  PR_BRANCH="$(printf '%s' "$PR_CONTEXT" | cut -f3)"
fi
BRANCH="$(lintgate_current_branch)"
if [ -n "${GITHUB_HEAD_REF:-}" ]; then
  BRANCH="$GITHUB_HEAD_REF"
elif [ -n "${GITHUB_REF_NAME:-}" ]; then
  BRANCH="$GITHUB_REF_NAME"
fi

if [ "$SCANNER_PATH" = "pysonar-scanner" ]; then
  if [ -n "$PR_NUMBER" ] && [ -n "$PR_BASE" ] && [ -n "$PR_BRANCH" ]; then
    SONAR_TOKEN="$SONAR_TOKEN" "$SCANNER_PATH" \
      -Dproject.home="$ROOT" \
      -read.project.config \
      -Dsonar.pullrequest.key="$PR_NUMBER" \
      -Dsonar.pullrequest.branch="$PR_BRANCH" \
      -Dsonar.pullrequest.base="$PR_BASE" >/dev/null
  else
    SONAR_TOKEN="$SONAR_TOKEN" "$SCANNER_PATH" \
      -Dproject.home="$ROOT" \
      -read.project.config \
      -Dsonar.branch.name="$BRANCH" >/dev/null
  fi
else
  if [ -n "$PR_NUMBER" ] && [ -n "$PR_BASE" ] && [ -n "$PR_BRANCH" ]; then
    SONAR_TOKEN="$SONAR_TOKEN" "$SCANNER_PATH" \
      -Dsonar.projectBaseDir="$ROOT" \
      -Dsonar.pullrequest.key="$PR_NUMBER" \
      -Dsonar.pullrequest.branch="$PR_BRANCH" \
      -Dsonar.pullrequest.base="$PR_BASE" >/dev/null
  else
    SONAR_TOKEN="$SONAR_TOKEN" "$SCANNER_PATH" \
      -Dsonar.projectBaseDir="$ROOT" \
      -Dsonar.branch.name="$BRANCH" >/dev/null
  fi
fi

if [ -n "$PR_NUMBER" ]; then
  STATUS_URL="${SONAR_HOST%/}/api/qualitygates/project_status?projectKey=${PROJECT_KEY}&pullRequest=${PR_NUMBER}"
else
  STATUS_URL="${SONAR_HOST%/}/api/qualitygates/project_status?projectKey=${PROJECT_KEY}&branch=${BRANCH}"
fi

QUALITY_STATUS="$(curl -fsS -u "${SONAR_TOKEN}:" "$STATUS_URL" 2>/dev/null \
  | python -c 'import json,sys; print(json.load(sys.stdin).get("projectStatus",{}).get("status",""))' 2>/dev/null || true)"
if [ -z "$QUALITY_STATUS" ] || [ "$QUALITY_STATUS" != "OK" ]; then
  echo "Sonar quality gate is ${QUALITY_STATUS:-UNKNOWN}" >&2
  exit 1
fi

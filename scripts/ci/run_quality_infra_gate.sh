#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [ -f ".env" ] && [ -z "${GITHUB_ACTIONS:-}" ]; then
  # shellcheck disable=SC1091
  . ".env"
fi

python -m lintgate.quality_infra --enforce "$ROOT"

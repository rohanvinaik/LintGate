#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "[1/5] Cleaning Lake cache (.lake)"
rm -rf .lake

echo "[2/5] Re-resolving dependencies"
lake update

echo "[3/5] Verifying pinned versions"
./scripts/lean-doctor.sh

echo "[4/5] Enforcing strict pin guard"
./scripts/lean-guard.sh

echo "[5/5] Rebuilding target module"
lake build MutationTheory.Full_Corpus_Compile

echo "[6/6] Done"
echo "Now run: Lean: Restart Server (or reload VS Code window)"

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PIN_FILES=(
  "lean-toolchain"
  "lakefile.toml"
  "lake-manifest.json"
)

echo "Locking pin files (read-only)..."
for file in "${PIN_FILES[@]}"; do
  [[ -f "$file" ]] || { echo "[ERROR] Missing $file"; exit 1; }
  chmod a-w "$file"
  echo "[LOCKED] $file"
done

echo "Done. To edit pins later, run: ./scripts/lean-unlock-pins.sh"

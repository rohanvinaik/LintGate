#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PIN_FILES=(
  "lean-toolchain"
  "lakefile.toml"
  "lake-manifest.json"
)

echo "Unlocking pin files (owner-writable)..."
for file in "${PIN_FILES[@]}"; do
  [[ -f "$file" ]] || { echo "[ERROR] Missing $file"; exit 1; }
  chmod u+w "$file"
  echo "[UNLOCKED] $file"
done

echo "Done. After edits, re-lock with: ./scripts/lean-lock-pins.sh"

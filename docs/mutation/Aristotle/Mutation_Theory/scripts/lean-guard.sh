#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

EXPECTED_LEAN="leanprover/lean4:v4.24.0"
EXPECTED_MATHLIB="f897ebcf72cd16f89ab4577d0c826cd14afaafc7"

fail() {
  printf "[GUARD FAIL] %s\n" "$1" >&2
  printf "Run: ./scripts/lean-reset.sh\n" >&2
  exit 1
}

[[ -f lean-toolchain ]] || fail "lean-toolchain not found"
actual_lean="$(tr -d '[:space:]' < lean-toolchain)"
[[ "$actual_lean" == "$EXPECTED_LEAN" ]] || fail "lean-toolchain mismatch: got '$actual_lean', expected '$EXPECTED_LEAN'"

[[ -f lakefile.toml ]] || fail "lakefile.toml not found"
grep -q "$EXPECTED_MATHLIB" lakefile.toml || fail "lakefile.toml does not pin expected mathlib commit"

[[ -f lake-manifest.json ]] || fail "lake-manifest.json not found (run lake update)"
grep -q "$EXPECTED_MATHLIB" lake-manifest.json || fail "lake-manifest.json does not contain expected mathlib commit"

[[ -d .lake/packages/mathlib/.git ]] || fail "mathlib checkout missing in .lake/packages (run lake update)"
checked_out_mathlib="$(git -C .lake/packages/mathlib rev-parse HEAD)"
[[ "$checked_out_mathlib" == "$EXPECTED_MATHLIB" ]] || fail "checked-out mathlib is '$checked_out_mathlib', expected '$EXPECTED_MATHLIB'"

printf "[GUARD OK] Lean/mathlib pins are consistent\n"

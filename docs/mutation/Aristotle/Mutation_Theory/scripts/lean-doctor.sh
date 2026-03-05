#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

EXPECTED_LEAN="leanprover/lean4:v4.24.0"
EXPECTED_MATHLIB="f897ebcf72cd16f89ab4577d0c826cd14afaafc7"

ok() { printf "[OK] %s\n" "$1"; }
warn() { printf "[WARN] %s\n" "$1"; }
err() { printf "[ERROR] %s\n" "$1"; }

if [[ ! -f lean-toolchain ]]; then
  err "lean-toolchain not found"
  exit 1
fi

actual_lean="$(tr -d '[:space:]' < lean-toolchain)"
if [[ "$actual_lean" == "$EXPECTED_LEAN" ]]; then
  ok "lean-toolchain is pinned to $EXPECTED_LEAN"
else
  warn "lean-toolchain is '$actual_lean' (expected '$EXPECTED_LEAN')"
fi

if [[ -f lakefile.toml ]]; then
  if grep -q "$EXPECTED_MATHLIB" lakefile.toml; then
    ok "lakefile.toml mathlib pin matches expected commit"
  else
    warn "lakefile.toml mathlib pin does not match expected commit"
  fi
else
  err "lakefile.toml not found"
  exit 1
fi

if [[ -f lake-manifest.json ]]; then
  if grep -q "$EXPECTED_MATHLIB" lake-manifest.json; then
    ok "lake-manifest.json mathlib revision matches expected commit"
  else
    warn "lake-manifest.json mathlib revision does not match expected commit"
  fi
else
  warn "lake-manifest.json not found (run: lake update)"
fi

if [[ -d .lake/packages/mathlib/.git ]]; then
  checked_out_mathlib="$(git -C .lake/packages/mathlib rev-parse HEAD)"
  if [[ "$checked_out_mathlib" == "$EXPECTED_MATHLIB" ]]; then
    ok "checked-out mathlib package matches expected commit"
  else
    warn "checked-out mathlib package is '$checked_out_mathlib' (expected '$EXPECTED_MATHLIB')"
  fi
else
  warn "mathlib package checkout missing (run: lake update)"
fi

ok "doctor completed"
printf "If warnings persist, run: ./scripts/lean-reset.sh\n"

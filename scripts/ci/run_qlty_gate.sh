#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [ -d "$HOME/.qlty/bin" ]; then
  export PATH="$HOME/.qlty/bin:$PATH"
fi

backup=""
had_shellcheckrc=0
if [ -e ".shellcheckrc" ]; then
  had_shellcheckrc=1
fi
if [ -f ".shellcheckrc" ] && [ ! -L ".shellcheckrc" ]; then
  backup=".shellcheckrc.lintgate.bak"
  cp ".shellcheckrc" "$backup"
fi

restore_shellcheckrc() {
  if [ "$had_shellcheckrc" = "0" ] && [ -e ".shellcheckrc" ]; then
    rm -f ".shellcheckrc"
  fi
  if [ -n "$backup" ] && [ -f "$backup" ] && [ -L ".shellcheckrc" ]; then
    rm ".shellcheckrc"
    mv "$backup" ".shellcheckrc"
  elif [ -n "$backup" ] && [ -f "$backup" ]; then
    rm "$backup"
  fi
}

trap restore_shellcheckrc EXIT

if ! qlty check --all --dry-run >/dev/null 2>&1; then
  qlty init --skip-plugins 2>/dev/null || true
fi

qlty check --all

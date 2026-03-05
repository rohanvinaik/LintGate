#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_FILE="$ROOT_DIR/THEOREM_INDEX.generated.md"

{
  echo "# Theorem Index (Generated)"
  echo
  echo "Generated from Lean docstrings and declarations."
  echo
  echo "| Declaration | File | Docstring |"
  echo "|---|---|---|"

  find "$ROOT_DIR/MutationTheory" -name "*.lean" | sort | while read -r file; do
    awk -v file="$file" '
      BEGIN { in_doc = 0; doc = "" }
      /^\/--/ {
        in_doc = 1
        doc = $0
        gsub(/^\/--[[:space:]]*/, "", doc)
        next
      }
      in_doc {
        if ($0 ~ /-\/$/) {
          line = $0
          gsub(/-\/$/, "", line)
          gsub(/^[[:space:]]*/, "", line)
          if (length(line) > 0) {
            doc = doc " " line
          }
          in_doc = 0
          next
        }
        line = $0
        gsub(/^[[:space:]]*/, "", line)
        doc = doc " " line
        next
      }
      /^[[:space:]]*(theorem|lemma|def)[[:space:]]+[[:alnum:]_]+/ {
        decl = $0
        sub(/^[[:space:]]*(theorem|lemma|def)[[:space:]]+/, "", decl)
        sub(/[[:space:]].*$/, "", decl)
        d = doc
        gsub(/\|/, "\\|", d)
        gsub(/`/, "\\`", d)
        if (d == "") d = ""
        printf("| `%s` | `%s` | %s |\n", decl, file, d)
        doc = ""
      }
    ' "$file"
  done
} > "$OUT_FILE"

echo "Wrote $OUT_FILE"

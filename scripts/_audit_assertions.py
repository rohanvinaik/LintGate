#!/usr/bin/env python3
"""Batch inspect_test_assertions audit across all test files.

Writes results incrementally to /tmp/lintgate_audit.jsonl so terminal crashes
don't lose progress. Run from the lintgate repo root.
"""

import json
import os
import sys

sys.path.insert(0, ".")
import contextlib

import mcp_server

OUT = "/tmp/lintgate_audit.jsonl"  # nosec B108

test_files = sorted(
    os.path.join(r, f)
    for r, _, files in os.walk("tests")
    for f in files
    if f.startswith("test_") and f.endswith(".py")
)

already_done = set()
if os.path.exists(OUT):
    with open(OUT) as fh:
        for line in fh:
            with contextlib.suppress(Exception):
                already_done.add(json.loads(line)["file"])
    print(f"Resuming from {len(already_done)} already-processed files")

total = len(test_files)
with open(OUT, "a") as out_fh:
    for i, tf in enumerate(test_files):
        if tf in already_done:
            continue
        try:
            raw = mcp_server.inspect_test_assertions(".", tf)
            data = json.loads(raw)
            summary = data.get("summary", {})
            funcs = data.get("test_functions", {})

            # Per-function weak assertions
            weak_funcs = []
            for fn, info in funcs.items():
                s_count = info.get("structural_count", 0)
                sem_count = info.get("semantic_count", 0)
                if s_count > 0:
                    kinds = [
                        a["kind"] for a in info.get("assertions", []) if a.get("strength", 1) < 0.7
                    ]
                    weak_funcs.append(
                        {"fn": fn, "structural": s_count, "semantic": sem_count, "kinds": kinds}
                    )

            record = {
                "file": tf,
                "tests": summary.get("total_tests", 0),
                "assertions": summary.get("total_assertions", 0),
                "semantic": summary.get("semantic_assertions", 0),
                "structural": summary.get("structural_assertions", 0),
                "ratio": summary.get("semantic_ratio", None),
                "weak_fns": weak_funcs,
                "error": None,
            }
        except Exception as e:
            record = {
                "file": tf,
                "tests": 0,
                "assertions": 0,
                "semantic": 0,
                "structural": 0,
                "ratio": None,
                "weak_fns": [],
                "error": str(e)[:120],
            }

        out_fh.write(json.dumps(record) + "\n")
        out_fh.flush()
        pct = (i + 1) / total * 100
        print(f"[{pct:5.1f}%] {record['ratio'] or 'ERR':>6}  {tf}", flush=True)

print(f"\nDone. Results at {OUT}")

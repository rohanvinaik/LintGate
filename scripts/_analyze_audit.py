#!/usr/bin/env python3
"""Analyze the pre-computed audit JSONL and print ranked findings."""

import collections
import json

INFILE = "/tmp/lintgate_audit.jsonl"  # nosec B108
results = []
with open(INFILE) as f:
    for line in f:
        line = line.strip()
        if line:
            results.append(json.loads(line))

errors = [r for r in results if r.get("error")]
no_assert = [r for r in results if not r.get("error") and r["assertions"] == 0]
has_data = [r for r in results if not r.get("error") and r["assertions"] > 0]
has_data.sort(key=lambda x: (x["ratio"] or 0, x["file"]))

p0 = [r for r in has_data if (r["ratio"] or 1) < 0.50]
p1 = [r for r in has_data if 0.50 <= (r["ratio"] or 1) < 0.70]
p2 = [r for r in has_data if 0.70 <= (r["ratio"] or 1) < 0.80]
good = [r for r in has_data if (r["ratio"] or 0) >= 0.80]

kind_freq: collections.Counter[str] = collections.Counter()
for r in has_data:
    for wf in r.get("weak_fns", []):
        for k in wf.get("kinds", []):
            kind_freq[k] += 1

total_assertions = sum(r["assertions"] for r in has_data)
total_structural = sum(r["structural"] for r in has_data)
total_semantic = sum(r["semantic"] for r in has_data)
overall_ratio = total_semantic / total_assertions if total_assertions else 0

print("=== SUMMARY ===")
print(f"Total files:         {len(results)}")
print(f"Errors (skipped):    {len(errors)}")
print(f"Zero assertions:     {len(no_assert)}")
print(f"Files with data:     {len(has_data)}")
print(f"Overall ratio:       {overall_ratio:.3f}  ({total_semantic}/{total_assertions} semantic)")
print(f"P0 (<0.50):          {len(p0)}")
print(f"P1 (0.50-0.70):      {len(p1)}")
print(f"P2 (0.70-0.80):      {len(p2)}")
print(f"Good (>=0.80):       {len(good)}")
print()

print("=== P0: ratio < 0.50 ===")
for r in p0:
    fname = r["file"].rsplit("/", 1)[-1]
    print(f"  {r['ratio']:.3f}  {r['structural']:3d}struct  {r['assertions']:4d}total  {fname}")
print()

print("=== P1: ratio 0.50-0.70 ===")
for r in p1:
    fname = r["file"].rsplit("/", 1)[-1]
    print(f"  {r['ratio']:.3f}  {r['structural']:3d}struct  {r['assertions']:4d}total  {fname}")
print()

print("=== ZERO-ASSERTION FILES ===")
for r in sorted(no_assert, key=lambda x: x["tests"], reverse=True):
    fname = r["file"].rsplit("/", 1)[-1]
    print(f"  {r['tests']:3d}tests  {fname}")
print()

print("=== DOMINANT STRUCTURAL ASSERTION KINDS ===")
for k, c in kind_freq.most_common(10):
    print(f"  {k}: {c}")
print()

# Top weak functions by absolute structural assertion count
fn_rows = []
for r in has_data:
    for wf in r.get("weak_fns", []):
        fn_rows.append((wf["structural"], r["file"].rsplit("/", 1)[-1], wf["fn"], wf["kinds"]))
fn_rows.sort(reverse=True)
print("=== TOP 25 WEAKEST TEST FUNCTIONS (by structural count) ===")
for struct, fname, fn, kinds in fn_rows[:25]:
    print(f"  {struct:3d}  {fname}::{fn}  {kinds}")
print()

if errors:
    print("=== ERRORS ===")
    for r in errors:
        print(f"  {r['file']}: {r['error']}")

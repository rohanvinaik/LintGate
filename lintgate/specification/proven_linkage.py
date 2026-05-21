"""Proven test-linkage cache.

When a test kills a mutant of function F, that test provably exercises F.
This is the strongest possible linkage signal — stronger than any name-based
heuristic, coverage trace, or static AST scan — because execution with
outcome change is the definition of "this test depends on this code."

Kill-proven linkages are persisted and consulted as Layer 0 of the test
discovery cascade. They accumulate over time, making discovery more
robust to naming drift with each run.

Schema (``.lintgate/mutation/linkage_proven.json``):

    {
        "<func_key>": {
            "entries": [
                {"test_file": "<abs path>", "test_function": "<name>",
                 "killed_mutants": <count>, "last_proven": <unix_ts>}
            ],
            "updated": <unix_ts>
        }
    }

``func_key`` uses the same canonical format as the mutation cache
(``<relpath>::<qualname>`` produced by ``lintgate.keys``).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

_LINKAGE_CACHE_FILE = ".lintgate/mutation/linkage_proven.json"


@dataclass
class ProvenEntry:
    test_file: str
    test_function: str
    killed_mutants: int = 1
    last_proven: int = 0


def cache_path(project_root: str) -> str:
    return os.path.join(project_root, _LINKAGE_CACHE_FILE)


def load_proven_entries(project_root: str, func_key: str) -> list[ProvenEntry]:
    """Return proven linkage entries for *func_key*, or [] if none exist."""
    path = cache_path(project_root)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    bucket = data.get(func_key)
    if not isinstance(bucket, dict):
        return []
    entries: list[ProvenEntry] = []
    for raw in bucket.get("entries", []):
        tf = raw.get("test_file") or ""
        fn = raw.get("test_function") or ""
        if not tf or not fn:
            continue
        entries.append(
            ProvenEntry(
                test_file=tf,
                test_function=fn,
                killed_mutants=int(raw.get("killed_mutants", 1)),
                last_proven=int(raw.get("last_proven", 0)),
            )
        )
    return entries


def record_kills(
    project_root: str,
    func_key: str,
    killed_pairs: list[tuple[str, str]],
) -> None:
    """Persist proven linkage for each (test_file, test_function) that killed a mutant.

    Merges into any existing entries — entries are never removed without
    being re-proven false (not yet implemented; kills are monotonic).
    """
    if not killed_pairs:
        return
    path = cache_path(project_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    data: dict[str, Any]
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            data = {}
    else:
        data = {}

    bucket = data.setdefault(func_key, {"entries": [], "updated": 0})
    existing: dict[tuple[str, str], dict[str, Any]] = {
        (e.get("test_file", ""), e.get("test_function", "")): e
        for e in bucket.get("entries", [])
    }
    now = int(time.time())
    for tf, fn in killed_pairs:
        key = (tf, fn)
        if key in existing:
            existing[key]["killed_mutants"] = int(existing[key].get("killed_mutants", 0)) + 1
            existing[key]["last_proven"] = now
        else:
            existing[key] = {
                "test_file": tf,
                "test_function": fn,
                "killed_mutants": 1,
                "last_proven": now,
            }
    bucket["entries"] = list(existing.values())
    bucket["updated"] = now

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def killed_pairs_from_result(
    killed_records: list[dict[str, Any]],
    test_name_to_file: dict[str, str],
) -> list[tuple[str, str]]:
    """Extract (test_file, test_function) pairs from killed_records.

    *killed_records* comes from ``ProfilingResult.to_dict()``. Each record
    has a ``killed_by_test`` field holding the test function name. Mapping
    back to a file requires the caller-supplied *test_name_to_file*, built
    from the loaded test callables at mutation time.

    Records without a resolvable file are dropped — we never fabricate a
    path. A proven linkage with an unverifiable test file is worthless.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for rec in killed_records:
        test_name = rec.get("killed_by_test") or ""
        if not test_name:
            continue
        # killed_by_test may be qualified like "TestClass.test_method" or
        # a nodeid form. Strip to the bare function name for lookup; also
        # try the qualified form.
        bare = test_name.rsplit(".", 1)[-1]
        path = test_name_to_file.get(test_name) or test_name_to_file.get(bare)
        if not path:
            continue
        key = (path, test_name)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)
    return pairs

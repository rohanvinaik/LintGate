"""Waiver parsing and application for the symbol coverage gate.

Handles per-symbol exemptions with expiry dates, glob patterns,
and exact-match lookups.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from lintgate.channels._symbol_types import SymbolCoverageWaiver, SymbolSpan


def parse_waivers(raw: Any) -> list[SymbolCoverageWaiver]:
    """Parse waiver config entries into SymbolCoverageWaiver objects."""
    if not isinstance(raw, list):
        return []

    waivers: list[SymbolCoverageWaiver] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        symbol = entry.get("symbol", "")
        reason = entry.get("reason", "")
        if not symbol or not reason:
            continue  # Strict: must have symbol and reason
        waivers.append(
            SymbolCoverageWaiver(
                symbol=str(symbol),
                reason=str(reason),
                expires=str(entry["expires"]) if entry.get("expires") else None,
            )
        )
    return waivers


def apply_waivers(
    targets: list[SymbolSpan],
    waivers: list[SymbolCoverageWaiver],
    today: date,
) -> tuple[list[SymbolSpan], list[tuple[str, SymbolCoverageWaiver]], list[SymbolCoverageWaiver]]:
    """Apply waivers to the target set.

    Returns (filtered_targets, applied_waivers, expired_waivers).
    """
    exact_waivers, glob_waivers, expired = _partition_waivers(waivers, today)

    filtered: list[SymbolSpan] = []
    applied: list[tuple[str, SymbolCoverageWaiver]] = []

    for target in targets:
        matched = _match_target_waiver(target, exact_waivers, glob_waivers)
        if matched is not None:
            applied.append((target.symbol_key, matched))
        else:
            filtered.append(target)

    return filtered, applied, expired


def _partition_waivers(
    waivers: list[SymbolCoverageWaiver],
    today: date,
) -> tuple[dict[str, SymbolCoverageWaiver], list[SymbolCoverageWaiver], list[SymbolCoverageWaiver]]:
    """Partition waivers into active (by symbol) and expired lists.

    Returns (active_waivers, glob_waivers, expired_waivers).
    Active waivers are split into exact-match dict and glob-pattern list.
    """
    expired: list[SymbolCoverageWaiver] = []
    exact_waivers: dict[str, SymbolCoverageWaiver] = {}
    glob_waivers: list[SymbolCoverageWaiver] = []

    for waiver in waivers:
        if waiver.expires:
            try:
                exp_date = date.fromisoformat(waiver.expires)
                if exp_date < today:
                    expired.append(waiver)
                    continue
            except ValueError:
                continue  # Invalid date format — skip waiver

        if "*" in waiver.symbol:
            glob_waivers.append(waiver)
        else:
            exact_waivers[waiver.symbol] = waiver

    return exact_waivers, glob_waivers, expired


def _match_target_waiver(
    target: SymbolSpan,
    exact_waivers: dict[str, SymbolCoverageWaiver],
    glob_waivers: list[SymbolCoverageWaiver],
) -> SymbolCoverageWaiver | None:
    """Match a target against exact and glob waivers. Returns matched waiver or None."""
    if target.symbol_key in exact_waivers:
        return exact_waivers[target.symbol_key]

    from fnmatch import fnmatch

    for gw in glob_waivers:
        if fnmatch(target.symbol_key, gw.symbol):
            return gw
    return None

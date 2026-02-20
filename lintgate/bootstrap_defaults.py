"""Battle-tested zero-state bootstrap defaults.

When theory extraction finds no project-specific claims, these curated defaults
provide actionable content from day one. Each item maps to a specific failure
pattern observed in real agent sessions — documented across the mneme
retrospective, iphone-recovery behavioral baseline, and LintGate field reports.

These are the fallbacks. When theory extraction produces real claims, those
claims take priority. The defaults exist so that bootstrap never produces
generic platitudes.
"""

from __future__ import annotations

# ── Anti-patterns ───────────────────────────────────────────────────
# Used by _select_actionable_anti_patterns() when no claims match
# _NEGATIVE_CUE_RE. Each item MUST:
#   1. Start with "Do not" (matches _NEGATIVE_CUE_RE)
#   2. Be under 260 characters (the truncation threshold)
#   3. Map to a documented failure pattern

ZERO_STATE_ANTI_PATTERNS: list[str] = [
    # From behavioral baseline: approach cycling
    (
        "Do not try a 4th approach without first enumerating all known constraints "
        "and verifying which ones the new approach actually addresses."
    ),
    # From behavioral baseline: serial constraint discovery
    (
        "Do not discover constraints one-at-a-time through failure — enumerate "
        "the full constraint space upfront by reading before acting."
    ),
    # From behavioral baseline: failure amnesia
    (
        "Do not re-attempt an approach that already failed unless the conditions "
        "that caused the failure have changed."
    ),
    # From mneme retrospective: root cause clustering
    (
        "Do not treat N instances of the same root cause as N separate problems — "
        "cluster issues by shared fix before diving into individual repairs."
    ),
    # From behavioral baseline: premature action
    (
        "Do not act before understanding — convert unbounded aesthetic tasks "
        "into bounded checklists before beginning work."
    ),
    # From mneme retrospective: ignoring layered signals
    (
        "Do not ignore layered signals (e.g., file-too-long + too-many-methods + "
        "clustered type errors) that compose into a single structural diagnosis."
    ),
    # From performance anti-pattern detection: structurally wrong complexity choices
    (
        "Do not use O(n²) algorithms when O(n) alternatives exist — "
        "quadratic membership checks on lists, re.compile inside loops, "
        "and sorted()[0] instead of min() are structural mistakes, not style issues."
    ),
]


# ── Facet fallbacks ─────────────────────────────────────────────────
# Used by _render_claude_md() when facet_summaries lack a key.
# Each value is a one-sentence distillation of what we've observed
# matters most in real sessions.

ZERO_STATE_FACET_FALLBACKS: dict[str, str] = {
    "core_theory": (
        "Understand before acting — orient on the constraint space before "
        "writing code, because the cost of a wrong approach compounds while "
        "the cost of reading is fixed."
    ),
    "problem_solving": (
        "Cluster before fixing — group issues by root cause, apply batch "
        "fixes to categories, and use layered signal composition to identify "
        "structural problems that individual findings obscure."
    ),
    "alignment": (
        "A change is aligned when it addresses root causes rather than "
        "symptoms, incorporates all known constraints before acting, and "
        "leaves the codebase in a state where the next session starts "
        "from a better position."
    ),
    "architecture": (
        "Maintain explicit module boundaries and stable interfaces. When "
        "signals converge on a single file or class (complexity + size + "
        "type errors), the diagnosis is structural — split rather than patch."
    ),
}

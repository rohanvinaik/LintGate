"""Regression tests for Phase 5 bug fixes.

Covers:
- P0: Recurrence output capping (state.py, agent_reporter.py)
- P1: Pattern bank clean-run tracking (pattern_bank.py)
- P1: Redefinition checker property/setter handling (redefinition_checker.py)
- P1: Theory extractor regex construction (theory_extractor.py)
- P1: Context auditor rule coverage matching (context_auditor.py)
- P2: Theory extraction .claude/rules scanning (theory_extractor.py)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Ensure lintgate is importable
_LINTGATE_ROOT = str(Path(__file__).resolve().parent.parent)
if _LINTGATE_ROOT not in sys.path:
    sys.path.insert(0, _LINTGATE_ROOT)

from lintgate.types import LintIssue  # noqa: E402


class TestRecurrenceOutputCapping(unittest.TestCase):
    """P0: Verify recurrence output is bounded to prevent context flooding."""

    def test_update_issue_memory_caps_at_default(self):
        """top_n defaults to 10, not None."""
        from lintgate.state import update_issue_memory

        tmpdir = tempfile.mkdtemp()
        try:
            # Pre-populate memory with 50 signatures
            from lintgate.state import ISSUE_MEMORY_DIR, _project_hash
            ISSUE_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            memory_path = ISSUE_MEMORY_DIR / _project_hash(tmpdir)
            signatures = {}
            for i in range(50):
                sig = f"ruff|F821|file_{i}.py|{i}"
                signatures[sig] = {
                    "count": 5,
                    "last_seen": time.time() - 100,
                    "linter": "ruff",
                    "kind": "F821",
                    "file": f"file_{i}.py",
                    "line": i,
                    "message": f"Undefined name 'x_{i}'",
                }
            with open(memory_path, "w") as f:
                json.dump({"signatures": signatures}, f)

            # Create issues matching all 50 signatures
            issues = [
                LintIssue(
                    linter="ruff", kind="F821",
                    message=f"Undefined name 'x_{i}'",
                    file=f"file_{i}.py", line=i, severity="blocking",
                )
                for i in range(50)
            ]

            result = update_issue_memory(tmpdir, issues)
            # Should be capped at 10 (the default)
            self.assertLessEqual(len(result["top_repeated"]), 10)
            self.assertEqual(result["repeated_issue_count"], 50)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_reporter_caps_recurrence_items(self):
        """agent_reporter caps iteration at 10 even if more are passed."""
        from lintgate.agent_reporter import _add_recurrence_section

        parts: list[str] = []
        summary = {
            "repeated_issue_count": 25,
            "top_repeated": [
                {
                    "linter": "ruff", "kind": "F821",
                    "file": f"file_{i}.py", "line": i,
                    "count": 5, "message": f"Error {i}",
                }
                for i in range(25)
            ],
        }
        _add_recurrence_section(parts, summary)
        # Header + at most 10 items = 11 lines max
        self.assertLessEqual(len(parts), 11)


class TestPatternBankCleanRuns(unittest.TestCase):
    """P1: Pattern bank must track clean runs to compute recency correctly."""

    def test_no_false_alert_with_clean_runs_between(self):
        """A pattern seen on runs 1, 4, 7 with clean runs between should NOT
        trigger 'recurring_across_runs' if recent window is 5 and only
        run 7 is in the last 5."""
        from lintgate.pattern_bank import PATTERN_BANK_DIR, _project_hash, update_pattern_bank

        tmpdir = tempfile.mkdtemp()
        try:
            # Pre-seed the bank with a known state:
            # Pattern appeared on runs 1 and 4 (old), clean runs 5, 6
            PATTERN_BANK_DIR.mkdir(parents=True, exist_ok=True)
            bank_path = PATTERN_BANK_DIR / _project_hash(tmpdir)

            bank = {
                "global_run_ids": ["1000", "2000", "3000", "4000", "5000", "6000"],
                "patterns": {
                    "ruff|F821": {
                        "linter": "ruff",
                        "kind": "F821",
                        "total_count": 4,
                        "first_seen": 1000,
                        "last_seen": 4000,
                        "run_history": [
                            {"run_id": "1000", "timestamp": 1000, "count": 2, "files": 1},
                            {"run_id": "4000", "timestamp": 4000, "count": 2, "files": 1},
                        ],
                    }
                },
                "updated_at": 6000,
            }
            with open(bank_path, "w") as f:
                json.dump(bank, f)

            # Now run 7 with one F821 issue
            issues = [
                LintIssue(
                    linter="ruff", kind="F821",
                    message="Undefined name 'x'",
                    file="test.py", line=1, severity="blocking",
                ),
            ]
            result = update_pattern_bank(tmpdir, issues)

            # With correct clean-run tracking:
            # Global runs: [2000, 3000, 4000, 5000, 6000, <new>]
            # Recent window (last 5): [3000, 4000, 5000, 6000, <new>]
            # Pattern appeared in: run 4000 and <new> => 2 of last 5
            # Threshold is 3, so should NOT trigger recurring_across_runs
            recurring_alerts = [
                a for a in result["alerted_patterns"]
                if a["alert_reason"] == "recurring_across_runs"
            ]
            self.assertEqual(len(recurring_alerts), 0,
                "Should not fire recurring alert when pattern only appeared "
                "in 2 of last 5 global runs")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
            # Clean up bank file
            bp = PATTERN_BANK_DIR / _project_hash(tmpdir)
            bp.unlink(missing_ok=True)


class TestRedefinitionCheckerPropertySetter(unittest.TestCase):
    """P1: Redefinition checker must not flag @property/@setter as duplicates."""

    def _check_source(self, source: str) -> list[LintIssue]:
        """Helper to run redefinition checker on a source string."""
        from lintgate.linters.redefinition_checker import _check_file
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                delete=False,
            ) as tmpfile:
                tmpfile.write(source)
                tmp_path = tmpfile.name
            return list(_check_file(tmp_path))
        finally:
            if tmp_path:
                os.unlink(tmp_path)

    def test_property_setter_not_flagged(self):
        """@property + @name.setter is valid Python, not a redefinition."""
        source = '''
class Config:
    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        self._name = value
'''
        issues = self._check_source(source)
        self.assertEqual(len(issues), 0,
            f"@property/@setter should not be flagged, got: {issues}")

    def test_property_deleter_not_flagged(self):
        """@property + @name.deleter is valid Python."""
        source = '''
class Resource:
    @property
    def handle(self):
        return self._handle

    @handle.deleter
    def handle(self):
        del self._handle
'''
        issues = self._check_source(source)
        self.assertEqual(len(issues), 0)

    def test_cached_property_not_flagged(self):
        """@cached_property should not be flagged."""
        source = '''
from functools import cached_property

class Expensive:
    @cached_property
    def result(self):
        return compute()

    @cached_property
    def other(self):
        return compute2()
'''
        issues = self._check_source(source)
        self.assertEqual(len(issues), 0)

    def test_actual_redefinition_still_caught(self):
        """A real redefinition (no property decorator) should still be caught."""
        source = '''
class Broken:
    def process(self):
        return 1

    def process(self):
        return 2
'''
        issues = self._check_source(source)
        self.assertEqual(len(issues), 1)
        self.assertIn("process", issues[0].message)


class TestTheoryExtractorRegex(unittest.TestCase):
    """P1: Theory extractor regex for 'DO NOT call X()' must produce valid regex."""

    def test_do_not_call_produces_valid_regex(self):
        """The generated regex should use \\s* not s* before the paren."""
        # Find the template and simulate a match
        from lintgate.theory_extractor import _RULE_TEMPLATES

        call_template = None
        for regex, kind, builder, conf in _RULE_TEMPLATES:
            if "DO NOT call" in regex:
                call_template = (regex, kind, builder, conf)
                break

        self.assertIsNotNone(call_template, "Should have a 'DO NOT call' template")
        regex, kind, builder, _ = call_template

        match = re.search(regex, "DO NOT call dangerous_api()")
        self.assertIsNotNone(match)

        pattern = builder(match)
        self.assertIsNotNone(pattern)
        # The critical fix: must have \s* not bare s*
        self.assertIn(r"\s*\(", pattern,
            f"Pattern should contain '\\s*\\(' but got: {pattern}")
        # The bug was producing "...apis*\(" — check the pattern doesn't
        # have a word char immediately before "s*\("
        self.assertIsNone(
            re.search(r"\w+s\*\\?\(", pattern),
            f"Pattern has word+s*( which is the old bug: {pattern}",
        )

        # Verify the pattern actually compiles and matches
        compiled = re.compile(pattern)
        self.assertTrue(compiled.search("dangerous_api("))
        self.assertTrue(compiled.search("dangerous_api ("))


class TestContextAuditorRuleCoverage(unittest.TestCase):
    """P1: Context auditor should count rules matched by pattern, not just message text."""

    def test_forbid_regex_covers_do_not_directive(self):
        """A LINTGATE_FORBID_REGEX with relevant keywords should count as coverage."""
        from lintgate.context_auditor import _check_rule_coverage

        checks: list[dict] = []
        suggestions: list[str] = []

        # Guidance with a DO NOT directive
        guidance = {
            "directives": {
                "do_not": [
                    "DO NOT create task-specific functions like solve_task_abc()"
                ],
            },
        }

        # An existing rule that covers it by pattern content
        existing_rules = [
            {
                "kind": "forbid_regex",
                "pattern": r"def\s+solve_task_[A-Za-z0-9_]*\s*\(",
                "severity": "blocking",
                "message": "Task-specific solver function detected",
                "source": "inferred:do_not_solve_task_prefix",
            }
        ]

        _check_rule_coverage(
            checks, suggestions, guidance, existing_rules,
            {"min_rule_coverage_pct": 50},
        )

        # Should report 1/1 covered (100%)
        self.assertEqual(len(checks), 1)
        check = checks[0]
        # The words "task" and "specific" and "functions" overlap
        self.assertIn("1/1", check["detail"],
            f"Should show 1/1 coverage, got: {check['detail']}")


class TestTheoryExtractorClaudeRules(unittest.TestCase):
    """P2: Theory extraction should scan .claude/rules/ docs."""

    def test_discovers_claude_rules_md_files(self):
        """Files in .claude/rules/*.md should be included in theory scan."""
        from lintgate.theory_extractor import _discover_md_files

        tmpdir = tempfile.mkdtemp()
        try:
            # Create project structure
            (Path(tmpdir) / "README.md").write_text("# Test Project\n")
            rules_dir = Path(tmpdir) / ".claude" / "rules"
            rules_dir.mkdir(parents=True)
            (rules_dir / "architecture.md").write_text(
                "# Architecture Rules\n\n"
                "The system uses compositional design.\n"
            )
            (rules_dir / "testing.md").write_text(
                "# Testing Rules\n\n"
                "All changes must have tests.\n"
            )

            found = _discover_md_files(tmpdir)
            basenames = [os.path.basename(p) for p in found]

            self.assertIn("README.md", basenames)
            self.assertIn("architecture.md", basenames)
            self.assertIn("testing.md", basenames)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_does_not_scan_other_claude_files(self):
        """Other .claude/ contents (transcripts, etc.) should not be scanned."""
        from lintgate.theory_extractor import _discover_md_files

        tmpdir = tempfile.mkdtemp()
        try:
            (Path(tmpdir) / "README.md").write_text("# Test\n")
            # Create a transcript file in .claude/ that should NOT be found
            claude_dir = Path(tmpdir) / ".claude"
            claude_dir.mkdir(parents=True)
            (claude_dir / "transcript.md").write_text("session data\n")

            found = _discover_md_files(tmpdir)
            basenames = [os.path.basename(p) for p in found]

            self.assertIn("README.md", basenames)
            self.assertNotIn("transcript.md", basenames)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestTheoryPackAPI(unittest.TestCase):
    """Regression tests for build_theory_pack() and get_theory_context()."""

    def _make_project_with_theory(self) -> str:
        """Create a temp project with theory-rich markdown docs."""
        tmpdir = tempfile.mkdtemp()

        # CLAUDE.md with rules and directives
        (Path(tmpdir) / "CLAUDE.md").write_text(
            "# Test Project\n\n"
            "## Architecture\n\n"
            "The system uses compositional design because monolithic "
            "approaches fail at scale.\n\n"
            "## Rules\n\n"
            "DO NOT create task-specific functions like solve_abc()\n"
            "MUST use the pipeline module for all data processing\n"
        )

        # Research-style doc with theory signals
        docs_dir = Path(tmpdir) / "docs"
        docs_dir.mkdir()
        (docs_dir / "THEORY.md").write_text(
            "# Core Theory\n\n"
            "## Hypothesis\n\n"
            "We hypothesize that elimination-based search is fundamentally "
            "more efficient than enumeration because the constraint space "
            "is structured.\n\n"
            "## Problem-Solving Approach\n\n"
            "Rather than searching through all possibilities, the system "
            "transforms intractable search into guided descent.\n\n"
            "## Anti-Patterns\n\n"
            "Using black-box functions will ruin the compositional "
            "architecture. Task-specific solutions bypass the learning "
            "networks and prevent transfer.\n\n"
            "## Key Abstractions\n\n"
            "We define **primitive operations** as the atomic building "
            "blocks of transformation. Each primitive is called a "
            "**selector** because it selects relevant features.\n"
        )

        # Journal with lessons
        (docs_dir / "JOURNAL.md").write_text(
            "# Development Journal\n\n"
            "## Lessons Learned\n\n"
            "**Lesson:** Validate every 50 lines rather than waiting "
            "until the end. The fix was to add iterative validation.\n\n"
            "## What Didn't Work\n\n"
            "Trying to write everything in one shot led to truncation.\n\n"
            "## What Worked\n\n"
            "Subagent decomposition at structural boundaries worked "
            "because each chunk is self-contained.\n"
        )

        return tmpdir

    def test_build_theory_pack_returns_all_fields(self):
        """Default build_theory_pack returns digest-first payload (no full profile)."""
        from lintgate.theory_extractor import build_theory_pack

        tmpdir = self._make_project_with_theory()
        try:
            pack = build_theory_pack(tmpdir)

            # Required fields
            self.assertIn("digest_text", pack)
            self.assertIn("digest_token_estimate", pack)
            self.assertIn("enforceable_rules", pack)
            self.assertIn("facet_summaries", pack)
            self.assertIn("anti_patterns", pack)
            self.assertIn("summary", pack)
            self.assertIn("validity", pack)
            self.assertNotIn("full_profile", pack)

            # Digest should be non-empty
            self.assertGreater(len(pack["digest_text"]), 50)
            self.assertGreater(pack["digest_token_estimate"], 0)

            # Should have summaries for all 6 facets
            self.assertEqual(len(pack["facet_summaries"]), 6)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_build_theory_pack_includes_full_profile_when_requested(self):
        """Caller can opt in to include full profile for Tier-2 usage."""
        from lintgate.theory_extractor import build_theory_pack

        tmpdir = self._make_project_with_theory()
        try:
            pack = build_theory_pack(tmpdir, include_full_profile=True)
            self.assertIn("full_profile", pack)
            self.assertIn("core_theory", pack["full_profile"])
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_facet_summaries_are_unique(self):
        """Each facet should get a different summary claim."""
        from lintgate.theory_extractor import build_theory_pack

        tmpdir = self._make_project_with_theory()
        try:
            pack = build_theory_pack(tmpdir)
            summaries = pack["facet_summaries"]

            # Filter out "(no theory content found)" — those are allowed to repeat
            real_summaries = [
                v for v in summaries.values()
                if v != "(no theory content found)"
            ]

            # Each real summary should be unique
            self.assertEqual(
                len(real_summaries), len(set(real_summaries)),
                f"Duplicate summaries found: {real_summaries}"
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_anti_patterns_extracted(self):
        """Anti-patterns from THEORY.md should be found."""
        from lintgate.theory_extractor import build_theory_pack

        tmpdir = self._make_project_with_theory()
        try:
            pack = build_theory_pack(tmpdir)
            anti_texts = " ".join(pack["anti_patterns"]).lower()

            # Should find "black-box" or "compositional" anti-pattern content
            has_relevant = (
                "black" in anti_texts or
                "compositional" in anti_texts or
                "task-specific" in anti_texts or
                "ruin" in anti_texts
            )
            self.assertTrue(has_relevant,
                f"Expected anti-pattern content, got: {pack['anti_patterns']}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_get_theory_context_by_facet(self):
        """get_theory_context filters by facet correctly."""
        from lintgate.theory_extractor import get_theory_context

        tmpdir = self._make_project_with_theory()
        try:
            result = get_theory_context(tmpdir, facet="core_theory")

            self.assertIn("claims", result)
            self.assertIn("total_matched", result)

            # All returned claims should be from core_theory
            for claim in result["claims"]:
                self.assertEqual(claim["facet"], "core_theory")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_get_theory_context_by_keywords(self):
        """get_theory_context filters by keyword correctly."""
        from lintgate.theory_extractor import get_theory_context

        tmpdir = self._make_project_with_theory()
        try:
            result = get_theory_context(
                tmpdir,
                keywords=["compositional", "elimination"],
                max_claims=3,
            )

            self.assertIn("claims", result)
            # Should find at least one match (THEORY.md and CLAUDE.md
            # both mention compositional/elimination)
            self.assertGreater(result["total_matched"], 0)

            # Each claim should contain at least one keyword
            for claim in result["claims"]:
                claim_lower = claim["claim"].lower()
                has_kw = any(
                    kw in claim_lower
                    for kw in ["compositional", "elimination"]
                )
                self.assertTrue(has_kw,
                    f"Claim doesn't match keywords: {claim['claim'][:80]}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_get_theory_context_reports_total_before_truncation(self):
        """total_matched should report full match count, not truncated count."""
        from lintgate.theory_extractor import get_theory_context

        tmpdir = self._make_project_with_theory()
        try:
            full = get_theory_context(tmpdir, facet="problem_solving", max_claims=50)
            truncated = get_theory_context(tmpdir, facet="problem_solving", max_claims=1)

            self.assertGreater(full["total_matched"], 0)
            self.assertEqual(len(truncated["claims"]), 1)
            self.assertGreaterEqual(full["total_matched"], truncated["total_matched"])
            self.assertTrue(truncated["truncated"])
            self.assertEqual(truncated["returned_count"], 1)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_hypothesis_detected_as_core_theory(self):
        """'We hypothesize that...' should be classified as core_theory."""
        from lintgate.theory_extractor import extract_theory

        tmpdir = self._make_project_with_theory()
        try:
            result = extract_theory(tmpdir)
            core_claims = []
            for entry in result["theory_profile"]["core_theory"]:
                core_claims.extend(entry["claims"])

            all_text = " ".join(core_claims).lower()
            self.assertIn("hypothesize", all_text,
                f"'hypothesize' not found in core_theory claims: "
                f"{core_claims[:3]}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_lesson_pattern_detected_as_problem_solving(self):
        """'**Lesson:**' pattern should be classified as problem_solving."""
        from lintgate.theory_extractor import extract_theory

        tmpdir = self._make_project_with_theory()
        try:
            result = extract_theory(tmpdir)
            ps_claims = []
            for entry in result["theory_profile"]["problem_solving"]:
                ps_claims.extend(entry["claims"])

            # Should find content from JOURNAL.md's "Lessons Learned" section
            all_text = " ".join(ps_claims).lower()
            has_lesson_content = (
                "validate" in all_text or
                "iterative" in all_text or
                "decomposition" in all_text
            )
            self.assertTrue(has_lesson_content,
                f"Expected lesson content in problem_solving: {ps_claims[:3]}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_digest_token_estimate_reasonable(self):
        """Digest token estimate should be in the 50-2000 range."""
        from lintgate.theory_extractor import build_theory_pack

        tmpdir = self._make_project_with_theory()
        try:
            pack = build_theory_pack(tmpdir)
            tokens = pack["digest_token_estimate"]
            self.assertGreaterEqual(tokens, 20,
                f"Token estimate too low: {tokens}")
            self.assertLessEqual(tokens, 2000,
                f"Token estimate too high: {tokens}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

"""Integration test: theory_scope controls theory extraction inclusion.

Generated wiki pages with ``theory_scope: false`` frontmatter must be
excluded from ``_discover_md_files()`` (via ``_has_frontmatter_opt_out``).
Promoted pages with ``theory_scope: true`` must be included.
"""

from __future__ import annotations

import os

from lintgate.theory_extractor import _discover_md_files, _has_frontmatter_opt_out


def test_theory_scope_false_excluded(tmp_path):
    """Page with theory_scope: false is skipped by _has_frontmatter_opt_out."""
    wiki_dir = tmp_path / ".lintgate" / "wiki"
    wiki_dir.mkdir(parents=True)

    page = wiki_dir / "Theory-Core.md"
    page.write_text(
        "---\n"
        "theory_scope: false\n"
        "pillar: theory\n"
        "generated_by: lintgate_wiki\n"
        "---\n"
        "# Core Thesis\n\nSome content.\n"
    )

    assert _has_frontmatter_opt_out(str(page)) is True


def test_theory_scope_true_included(tmp_path):
    """Page with theory_scope: true is NOT opted out."""
    wiki_dir = tmp_path / ".lintgate" / "wiki"
    wiki_dir.mkdir(parents=True)

    page = wiki_dir / "Promoted-Page.md"
    page.write_text(
        "---\n"
        "theory_scope: true\n"
        "pillar: theory\n"
        "generated_by: lintgate_wiki\n"
        "---\n"
        "# Promoted Content\n\nThis should be extracted.\n"
    )

    assert _has_frontmatter_opt_out(str(page)) is False


def test_discover_md_files_respects_theory_scope(tmp_path):
    """_discover_md_files includes .lintgate/wiki/ but _parse_document skips opt-out pages."""
    wiki_dir = tmp_path / ".lintgate" / "wiki"
    wiki_dir.mkdir(parents=True)

    # Create two wiki pages
    excluded = wiki_dir / "Excluded.md"
    excluded.write_text("---\ntheory_scope: false\n---\n# Excluded\n\nNot for theory.\n")

    included = wiki_dir / "Included.md"
    included.write_text("---\ntheory_scope: true\n---\n# Included\n\nFor theory.\n")

    # _discover_md_files finds both files (it doesn't filter by frontmatter)
    found = _discover_md_files(str(tmp_path))
    found_basenames = [os.path.basename(f) for f in found]
    assert "Excluded.md" in found_basenames
    assert "Included.md" in found_basenames

    # But _has_frontmatter_opt_out differentiates them
    excluded_path = str(wiki_dir / "Excluded.md")
    included_path = str(wiki_dir / "Included.md")
    assert _has_frontmatter_opt_out(excluded_path) is True
    assert _has_frontmatter_opt_out(included_path) is False


def test_no_frontmatter_not_opted_out(tmp_path):
    """File without frontmatter is not opted out."""
    md = tmp_path / "plain.md"
    md.write_text("# Just a heading\n\nContent.\n")

    assert _has_frontmatter_opt_out(str(md)) is False

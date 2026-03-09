"""Tests for wiki link integrity checker."""

from __future__ import annotations

from lintgate.wiki.link_checker import check_config_completeness, check_wiki_links


def _setup_manifest(tmp_path, yaml_content):
    """Write a manifest and return project root."""
    lg_dir = tmp_path / ".lintgate"
    lg_dir.mkdir()
    (lg_dir / "wiki_manifest.yaml").write_text(yaml_content)
    return str(tmp_path)


def _setup_wiki_page(tmp_path, page_name, content):
    """Write a wiki page to .lintgate/wiki/."""
    wiki_dir = tmp_path / ".lintgate" / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / f"{page_name}.md").write_text(content)


MANIFEST_YAML = """\
version: 1
pages:
  - name: Intro
    title: Introduction
    pillar: guide
    order: 1
    sources: []
  - name: Advanced
    title: Advanced Topics
    pillar: guide
    order: 2
    sources: []
"""


def test_check_links_valid(tmp_path):
    root = _setup_manifest(tmp_path, MANIFEST_YAML)
    _setup_wiki_page(tmp_path, "Home", "# Home\n\nSee [Intro](Intro) and [Advanced](Advanced).\n")
    _setup_wiki_page(tmp_path, "Intro", "# Intro\n\nSee [Home](Home).\n")
    _setup_wiki_page(tmp_path, "Advanced", "# Advanced\n\nContent.\n")

    result = check_wiki_links(root)
    assert result.ok
    assert result.pages_checked == 3
    assert result.links_checked == 3


def test_check_links_broken(tmp_path):
    root = _setup_manifest(tmp_path, MANIFEST_YAML)
    _setup_wiki_page(tmp_path, "Intro", "# Intro\n\nSee [missing](Nonexistent-Page).\n")

    result = check_wiki_links(root)
    assert not result.ok
    assert len(result.errors) >= 1
    broken = [e for e in result.errors if e.kind == "broken"]
    assert len(broken) == 1
    assert broken[0].target == "Nonexistent-Page"


def test_check_links_orphan_detection(tmp_path):
    root = _setup_manifest(tmp_path, MANIFEST_YAML)
    _setup_wiki_page(tmp_path, "Intro", "# Intro\n\nContent.\n")
    _setup_wiki_page(tmp_path, "Orphan-Page", "# Orphan\n\nNot in manifest.\n")

    result = check_wiki_links(root)
    orphans = [e for e in result.errors if e.kind == "orphan"]
    assert len(orphans) == 1
    assert orphans[0].source_page == "Orphan-Page"


def test_check_links_missing_materialized(tmp_path):
    root = _setup_manifest(tmp_path, MANIFEST_YAML)
    # Only materialize Intro, not Advanced
    _setup_wiki_page(tmp_path, "Intro", "# Intro\n\nContent.\n")

    result = check_wiki_links(root)
    missing = [e for e in result.errors if e.kind == "missing_config"]
    assert len(missing) >= 1
    targets = [e.target for e in missing]
    assert "Advanced" in targets


def test_check_links_no_manifest(tmp_path):
    result = check_wiki_links(str(tmp_path))
    assert not result.ok
    assert result.errors[0].kind == "missing_config"


def test_check_links_no_wiki_dir(tmp_path):
    root = _setup_manifest(tmp_path, MANIFEST_YAML)
    # No .lintgate/wiki/ directory
    result = check_wiki_links(root)
    # Should not crash, just report nothing checked
    assert result.pages_checked == 0


def test_check_links_to_dict(tmp_path):
    root = _setup_manifest(tmp_path, MANIFEST_YAML)
    _setup_wiki_page(tmp_path, "Intro", "# Intro\n\nSee [bad](Missing).\n")

    result = check_wiki_links(root)
    d = result.to_dict()
    assert isinstance(d, dict)
    assert "ok" in d
    assert "error_count" in d
    assert "errors" in d


# ─── Config completeness ─────────────────────────────────────────────────


def test_config_completeness_all_registered(tmp_path):
    """No issues when all docs/wiki/ files are in the manifest."""
    root = _setup_manifest(
        tmp_path,
        """\
version: 1
pages:
  - name: Guide
    title: Guide
    pillar: docs
    sources:
      - file: docs/wiki/guide.md
        kind: file
""",
    )
    wiki_src = tmp_path / "docs" / "wiki"
    wiki_src.mkdir(parents=True)
    (wiki_src / "guide.md").write_text("# Guide\n\nContent.\n")

    issues = check_config_completeness(root)
    assert issues == []


def test_config_completeness_unregistered(tmp_path):
    root = _setup_manifest(tmp_path, MANIFEST_YAML)
    wiki_src = tmp_path / "docs" / "wiki"
    wiki_src.mkdir(parents=True)
    (wiki_src / "orphan.md").write_text("# Orphan\n")

    issues = check_config_completeness(root)
    assert len(issues) == 1
    assert "orphan.md" in issues[0]["file"]


def test_config_completeness_skips_underscored(tmp_path):
    """Files starting with _ (sidebar, footer, template) are skipped."""
    root = _setup_manifest(tmp_path, MANIFEST_YAML)
    wiki_src = tmp_path / "docs" / "wiki"
    wiki_src.mkdir(parents=True)
    (wiki_src / "_Sidebar.md").write_text("sidebar\n")
    (wiki_src / "_Footer.md").write_text("footer\n")

    issues = check_config_completeness(root)
    assert issues == []

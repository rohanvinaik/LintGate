"""Tests for mcp_tools/wiki_tools.py helper functions."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mcp_tools.wiki_tools import (
    _detect_site_title,
    _do_wiki_materialize,
    _do_wiki_publish,
    _load_compass,
    _load_theory,
    _write_pages,
)

# ---------------------------------------------------------------------------
# _detect_site_title
# ---------------------------------------------------------------------------


class TestDetectSiteTitle:
    def test_extracts_repo_name_from_https_remote(self):
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="https://github.com/acme/widgetlib.git\n"
        )
        with patch("subprocess.run", return_value=proc):
            assert _detect_site_title("/tmp/proj") == "widgetlib"

    def test_strips_trailing_slash_and_git_suffix(self):
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="https://github.com/org/my-repo.git/\n"
        )
        with patch("subprocess.run", return_value=proc):
            assert _detect_site_title("/tmp/proj") == "my-repo"

    def test_falls_back_to_dirname_on_git_failure(self):
        proc = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="error")
        with patch("subprocess.run", return_value=proc):
            assert _detect_site_title("/tmp/my-project") == "my-project"

    def test_falls_back_to_dirname_on_exception(self):
        with patch("subprocess.run", side_effect=OSError("no git")):
            assert _detect_site_title("/opt/cool_tool") == "cool_tool"


# ---------------------------------------------------------------------------
# _load_theory
# ---------------------------------------------------------------------------


class TestLoadTheory:
    def test_returns_theory_profile_on_success(self):
        fake_profile = {"core_theory": ["claim1"]}
        with patch(
            "mcp_tools.wiki_tools.extract_theory",
            return_value={"theory_profile": fake_profile},
            create=True,
        ), patch(
            "lintgate.theory_extractor.extract_theory",
            return_value={"theory_profile": fake_profile},
        ):
                result = _load_theory("/tmp/proj")
                assert result == fake_profile

    def test_returns_none_on_import_error(self):
        with patch(
            "lintgate.theory_extractor.extract_theory",
            side_effect=ImportError("no module"),
        ):
            assert _load_theory("/tmp/proj") is None

    def test_returns_none_on_runtime_error(self):
        with patch(
            "lintgate.theory_extractor.extract_theory",
            side_effect=RuntimeError("oops"),
        ):
            assert _load_theory("/tmp/proj") is None


# ---------------------------------------------------------------------------
# _load_compass
# ---------------------------------------------------------------------------


class TestLoadCompass:
    def test_returns_dict_on_success(self):
        mock_state = MagicMock()
        mock_state.to_dict.return_value = {"direction": "north"}
        with patch("lintgate.compass_io.load_compass", return_value=mock_state):
            assert _load_compass("/tmp/proj") == {"direction": "north"}

    def test_returns_none_when_compass_is_none(self):
        with patch("lintgate.compass_io.load_compass", return_value=None):
            assert _load_compass("/tmp/proj") is None

    def test_returns_none_on_exception(self):
        with patch("lintgate.compass_io.load_compass", side_effect=Exception("broken")):
            assert _load_compass("/tmp/proj") is None


# ---------------------------------------------------------------------------
# _do_wiki_materialize
# ---------------------------------------------------------------------------


class TestDoWikiMaterialize:
    def test_returns_error_when_no_manifest(self):
        with patch("lintgate.wiki.manifest.load_manifest", return_value=None):
            result = _do_wiki_materialize("/tmp/proj", "", False)
            assert "error" in result
            assert "wiki manifest" in result["error"].lower() or "manifest" in result["error"]

    def test_dry_run_returns_page_summaries(self):
        mock_manifest = MagicMock()
        page = SimpleNamespace(
            name="overview",
            pillar="architecture",
            content="# Hello\nWorld",
            source_files=["a.md", "b.md"],
            theory_scope="core",
        )
        with (
            patch("lintgate.wiki.manifest.load_manifest", return_value=mock_manifest),
            patch("lintgate.wiki.composer.compose_all_pages", return_value=[page]),
            patch("mcp_tools.wiki_tools._load_theory", return_value=None),
            patch("mcp_tools.wiki_tools._load_compass", return_value=None),
        ):
            result = _do_wiki_materialize("/tmp/proj", "", False)
            assert result["mode"] == "dry-run"
            assert result["pages_count"] == 1
            assert result["pages"][0]["page"] == "overview"
            assert result["pages"][0]["content_length"] == len("# Hello\nWorld")
            assert result["pages"][0]["sources"] == 2

    def test_page_filter_by_name(self):
        mock_manifest = MagicMock()
        p1 = SimpleNamespace(
            name="design",
            pillar="arch",
            content="x",
            source_files=[],
            theory_scope=None,
        )
        p2 = SimpleNamespace(
            name="ops",
            pillar="ops",
            content="y",
            source_files=[],
            theory_scope=None,
        )
        with (
            patch("lintgate.wiki.manifest.load_manifest", return_value=mock_manifest),
            patch("lintgate.wiki.composer.compose_all_pages", return_value=[p1, p2]),
            patch("mcp_tools.wiki_tools._load_theory", return_value=None),
            patch("mcp_tools.wiki_tools._load_compass", return_value=None),
        ):
            result = _do_wiki_materialize("/tmp/proj", "design", False)
            assert result["pages_count"] == 1
            assert result["pages"][0]["page"] == "design"


# ---------------------------------------------------------------------------
# _write_pages
# ---------------------------------------------------------------------------


class TestWritePages:
    def test_writes_files_and_returns_results(self, tmp_path):
        wiki_dir = str(tmp_path / "wiki")
        project_root = str(tmp_path)

        page = SimpleNamespace(
            name="guide",
            pillar="howto",
            content="# Guide\nContent here",
            source_files=["src.md"],
        )

        mock_manifest = MagicMock()
        mock_manifest.pages = [SimpleNamespace(name="guide")]
        mock_manifest.manifest_hash_for_page.return_value = "abc123"

        mock_freshness_state = MagicMock()
        mock_freshness_state.pages = {}

        with (
            patch("lintgate.wiki.freshness.load_freshness_state", return_value=mock_freshness_state),
            patch("lintgate.wiki.freshness.save_freshness_state"),
            patch("lintgate.wiki.freshness._section_contents_for_page", return_value={}),
            patch(
                "lintgate.wiki.freshness.build_page_freshness",
                return_value="freshness_obj",
            ),
        ):
            results = _write_pages(project_root, mock_manifest, [page], wiki_dir)

        assert len(results) == 1
        assert results[0]["page"] == "guide"
        assert results[0]["pillar"] == "howto"
        assert results[0]["content_length"] == len("# Guide\nContent here")
        assert results[0]["sources"] == 1
        # Verify file was written
        import os

        assert os.path.isfile(os.path.join(wiki_dir, "guide.md"))


# ---------------------------------------------------------------------------
# _do_wiki_publish
# ---------------------------------------------------------------------------


class TestDoWikiPublish:
    def test_returns_error_when_no_manifest(self):
        with patch("lintgate.wiki.manifest.load_manifest", return_value=None):
            result = _do_wiki_publish("/tmp/proj", "_site", True, "", "")
            assert "error" in result

    def test_returns_publish_result(self):
        mock_manifest = MagicMock()
        published_page = SimpleNamespace(name="index", slug="index", html_size=512)
        publish_result = SimpleNamespace(
            pages=[published_page],
            sitemap_written=True,
            link_errors=[],
        )

        with (
            patch("lintgate.wiki.manifest.load_manifest", return_value=mock_manifest),
            patch("lintgate.wiki.composer.compose_all_pages", return_value=[]),
            patch("lintgate.wiki.pages_publisher.publish_pages", return_value=publish_result),
            patch("mcp_tools.wiki_tools._load_theory", return_value=None),
            patch("mcp_tools.wiki_tools._load_compass", return_value=None),
        ):
            result = _do_wiki_publish("/tmp/proj", "_site", True, "My Wiki", "")
            assert result["pages_published"] == 1
            assert result["pages"][0]["name"] == "index"
            assert result["sitemap"] is True
            assert result["link_check"] == "PASS"

    def test_link_check_fail_surfaces_errors(self):
        mock_manifest = MagicMock()
        publish_result = SimpleNamespace(
            pages=[],
            sitemap_written=False,
            link_errors=["broken link to /foo"],
        )

        with (
            patch("lintgate.wiki.manifest.load_manifest", return_value=mock_manifest),
            patch("lintgate.wiki.composer.compose_all_pages", return_value=[]),
            patch("lintgate.wiki.pages_publisher.publish_pages", return_value=publish_result),
            patch("mcp_tools.wiki_tools._load_theory", return_value=None),
            patch("mcp_tools.wiki_tools._load_compass", return_value=None),
        ):
            result = _do_wiki_publish("/tmp/proj", "_site", True, "T", "")
            assert result["link_check"] == "FAIL"
            assert result["link_errors"] == ["broken link to /foo"]

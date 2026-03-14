"""Tests for lintgate/context/auditor_path_refs.py.

Covers path reference extraction, dead path detection,
generated pattern matching, and check_path_references pipeline.
"""

from __future__ import annotations

from lintgate.context.auditor_path_refs import (
    _detect_generated_patterns,
    _find_bare_name_in_project,
    _matches_generated_pattern,
    check_path_references,
    extract_path_refs,
    find_dead_paths,
)

# ── extract_path_refs ────────────────────────────────────────────


class TestExtractPathRefs:
    def test_simple_path(self):
        refs = extract_path_refs("See `src/main.py` for details.")
        assert refs == ["src/main.py"]

    def test_multiple_paths(self):
        refs = extract_path_refs("`src/a.py` and `lib/b.yaml`")
        assert "src/a.py" in refs
        assert "lib/b.yaml" in refs

    def test_ignores_urls(self):
        refs = extract_path_refs("Visit `https://example.com/path`")
        assert refs == []

    def test_ignores_shell_commands(self):
        refs = extract_path_refs("Run `python -m pytest`")
        assert refs == []

    def test_ignores_code_blocks(self):
        text = "```python\nfrom src/mod import foo\n```\nSee `src/real.py`"
        refs = extract_path_refs(text)
        assert refs == ["src/real.py"]

    def test_ignores_tree_chars(self):
        refs = extract_path_refs("`├── src/foo.py`")
        assert refs == []

    def test_ignores_plain_names(self):
        refs = extract_path_refs("`FooClass` is important")
        assert refs == []

    def test_extension_only_no_slash(self):
        refs = extract_path_refs("See `config.yaml` for settings")
        assert refs == ["config.yaml"]

    def test_ignores_hf_model_ids(self):
        refs = extract_path_refs("Use `meta-llama/Llama-3.1-8B`")
        assert refs == []

    def test_path_with_spaces_no_slash_ignored(self):
        refs = extract_path_refs("`some command without slash.py`")
        assert refs == []

    def test_empty_text(self):
        assert extract_path_refs("") == []

    def test_relative_dot_path(self):
        refs = extract_path_refs("See `./src/main.py`")
        assert refs == ["./src/main.py"]

    def test_custom_scheme_url_ignored(self):
        refs = extract_path_refs("Open `vscode://file/path`")
        assert refs == []


# ── _detect_generated_patterns ───────────────────────────────────


class TestDetectGeneratedPatterns:
    def test_python_project(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]")
        patterns = _detect_generated_patterns(str(tmp_path))
        assert "dist" in patterns
        assert "*.egg-info" in patterns

    def test_node_project(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        patterns = _detect_generated_patterns(str(tmp_path))
        assert "node_modules" in patterns
        assert "dist" in patterns

    def test_rust_project(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]")
        patterns = _detect_generated_patterns(str(tmp_path))
        assert "target" in patterns

    def test_no_markers(self, tmp_path):
        patterns = _detect_generated_patterns(str(tmp_path))
        assert patterns == []

    def test_deduplicates(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]")
        (tmp_path / "Makefile").write_text("all:")
        patterns = _detect_generated_patterns(str(tmp_path))
        # Both pyproject and Makefile add "build" — should be deduped
        assert patterns.count("build") == 1


# ── _matches_generated_pattern ───────────────────────────────────


class TestMatchesGeneratedPattern:
    def test_exact_match(self):
        assert _matches_generated_pattern("dist", ["dist"]) is True

    def test_glob_match(self):
        assert _matches_generated_pattern("dist/bundle.js", ["dist/*"]) is True

    def test_egg_info_glob(self):
        assert _matches_generated_pattern("mypackage.egg-info", ["*.egg-info"]) is True

    def test_no_match(self):
        assert _matches_generated_pattern("src/main.py", ["dist", "build"]) is False

    def test_dotslash_prefix_stripped(self):
        assert _matches_generated_pattern("./dist/file.js", ["dist/*"]) is True

    def test_bare_name_against_pattern(self):
        assert _matches_generated_pattern("target/debug/app", ["target"]) is True


# ── find_dead_paths ──────────────────────────────────────────────


class TestFindDeadPaths:
    def test_existing_path_not_dead(self, tmp_path):
        (tmp_path / "README.md").write_text("hi")
        dead = find_dead_paths(["README.md"], str(tmp_path))
        assert dead == []

    def test_missing_path_is_dead(self, tmp_path):
        dead = find_dead_paths(["nonexistent.py"], str(tmp_path))
        assert dead == ["nonexistent.py"]

    def test_glob_patterns_skipped(self, tmp_path):
        dead = find_dead_paths(["src/*.py"], str(tmp_path))
        assert dead == []

    def test_generated_pattern_skipped(self, tmp_path):
        dead = find_dead_paths(["dist/bundle.js"], str(tmp_path), ["dist/*"])
        assert dead == []

    def test_bare_name_found_in_project(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "utils.py").write_text("pass")
        dead = find_dead_paths(["utils.py"], str(tmp_path))
        assert dead == []

    def test_bare_name_not_found(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "other.py").write_text("pass")
        dead = find_dead_paths(["utils.py"], str(tmp_path))
        assert dead == ["utils.py"]

    def test_dotslash_prefix(self, tmp_path):
        (tmp_path / "config.yaml").write_text("key: val")
        dead = find_dead_paths(["./config.yaml"], str(tmp_path))
        assert dead == []


# ── _find_bare_name_in_project ───────────────────────────────────


class TestFindBareNameInProject:
    def test_found_at_root(self, tmp_path):
        (tmp_path / "utils.py").write_text("")
        assert _find_bare_name_in_project("utils.py", str(tmp_path)) is True

    def test_found_in_src(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "helpers.py").write_text("")
        assert _find_bare_name_in_project("helpers.py", str(tmp_path)) is True

    def test_not_found(self, tmp_path):
        assert _find_bare_name_in_project("missing.py", str(tmp_path)) is False

    def test_depth_limit(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        (deep / "hidden.py").write_text("")
        # Depth > 3 should be skipped
        assert _find_bare_name_in_project("hidden.py", str(tmp_path)) is False


# ── check_path_references ────────────────────────────────────────


class TestCheckPathReferences:
    def test_no_refs_passes(self, tmp_path):
        checks: list = []
        suggestions: list = []
        check_path_references(checks, suggestions, "No paths here", str(tmp_path), {})
        assert len(checks) == 1
        assert checks[0]["status"] == "pass"

    def test_all_valid_refs_passes(self, tmp_path):
        (tmp_path / "README.md").write_text("hi")
        checks: list = []
        suggestions: list = []
        check_path_references(checks, suggestions, "See `README.md`", str(tmp_path), {})
        assert any(c["status"] == "pass" for c in checks)

    def test_dead_refs_warns(self, tmp_path):
        checks: list = []
        suggestions: list = []
        check_path_references(checks, suggestions, "See `nonexistent/file.py`", str(tmp_path), {})
        assert any(c["status"] == "warn" for c in checks)
        assert len(suggestions) > 0

    def test_too_many_refs_warns(self, tmp_path):
        # Create paths and reference them
        paths = " ".join(f"`path_{i}/file.py`" for i in range(60))
        checks: list = []
        suggestions: list = []
        check_path_references(
            checks, suggestions, paths, str(tmp_path), {"max_path_references": 50}
        )
        volume_checks = [c for c in checks if c["check"] == "path_reference_volume"]
        assert len(volume_checks) == 1
        assert volume_checks[0]["status"] == "warn"

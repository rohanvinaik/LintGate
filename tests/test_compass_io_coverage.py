"""Tests for lintgate/compass_io.py — persistence, migration, and helpers."""
from __future__ import annotations

import time

import pytest
import yaml

from lintgate.compass import (
    AXIS_NAMES,
    CompassAxis,
    CompassClaim,
    CompassDirective,
    CompassState,
    GapReport,
)
from lintgate.compass_io import (
    _derive_directives,
    _extract_text_from_item,
    _map_facet_claims,
    _score_axes,
    load_compass,
    migrate_from_theory_profile,
    reset_compass,
    save_compass,
)


# ── load_compass ────────────────────────────────────────────────────


class TestLoadCompass:
    def test_returns_none_when_file_missing(self, tmp_path):
        result = load_compass(str(tmp_path))
        assert result is None

    def test_loads_valid_yaml(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        state = CompassState(
            version=1,
            axes={"problem": CompassAxis(name="problem", depth=2)},
            forged_at=1000.0,
        )
        path = claude_dir / "compass.yaml"
        with open(path, "w") as f:
            yaml.dump(state.to_dict(), f)

        loaded = load_compass(str(tmp_path))
        assert loaded is not None
        assert loaded.version == 1
        assert "problem" in loaded.axes
        assert loaded.axes["problem"].depth == 2

    def test_returns_none_on_corrupt_yaml(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        path = claude_dir / "compass.yaml"
        path.write_text(": :\n  - [invalid\n")
        result = load_compass(str(tmp_path))
        assert result is None

    def test_returns_none_when_yaml_unavailable(self, tmp_path, monkeypatch):
        import lintgate.compass_io as cio

        monkeypatch.setattr(cio, "_YAML_AVAILABLE", False)
        result = load_compass(str(tmp_path))
        assert result is None


# ── save_compass ────────────────────────────────────────────────────


class TestSaveCompass:
    def test_saves_and_creates_directory(self, tmp_path):
        state = CompassState(version=1, forged_at=12345.0)
        path = save_compass(str(tmp_path), state)
        assert path.exists()
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["version"] == 1
        assert data["forged_at"] == 12345.0

    def test_raises_when_yaml_unavailable(self, tmp_path, monkeypatch):
        import lintgate.compass_io as cio

        monkeypatch.setattr(cio, "_YAML_AVAILABLE", False)
        with pytest.raises(RuntimeError, match="PyYAML"):
            save_compass(str(tmp_path), CompassState())

    def test_roundtrip(self, tmp_path):
        claims = [
            CompassClaim(text="Test claim", confidence=0.9, origin_facet="core_theory"),
        ]
        axes = {
            "problem": CompassAxis(name="problem", claims=claims, depth=1, summary="Test claim"),
            "solution": CompassAxis(name="solution"),
        }
        directives = [CompassDirective(kind="toward", text="Do X", source="solution")]
        state = CompassState(
            version=1,
            axes=axes,
            directives=directives,
            forged_at=time.time(),
        )
        save_compass(str(tmp_path), state)
        loaded = load_compass(str(tmp_path))
        assert loaded is not None
        assert loaded.axes["problem"].claims[0].text == "Test claim"
        assert loaded.directives[0].kind == "toward"


# ── reset_compass ───────────────────────────────────────────────────


class TestResetCompass:
    def test_returns_none_when_no_file(self, tmp_path):
        result = reset_compass(str(tmp_path))
        assert result is None

    def test_deletes_existing_file(self, tmp_path):
        state = CompassState(version=1)
        save_compass(str(tmp_path), state)
        compass_path = tmp_path / ".claude" / "compass.yaml"
        assert compass_path.exists()
        result = reset_compass(str(tmp_path))
        assert result is not None
        assert not compass_path.exists()


# ── _extract_text_from_item ─────────────────────────────────────────


class TestExtractTextFromItem:
    def test_dict_with_first_key(self):
        assert _extract_text_from_item({"pattern": "hello"}, "pattern", "text") == "hello"

    def test_dict_with_second_key(self):
        assert _extract_text_from_item({"text": "world"}, "pattern", "text") == "world"

    def test_dict_with_no_matching_key(self):
        assert _extract_text_from_item({"other": "val"}, "pattern", "text") == ""

    def test_string_input(self):
        assert _extract_text_from_item("  raw string  ", "k") == "raw string"

    def test_non_dict_non_string(self):
        assert _extract_text_from_item(42, "k") == ""
        assert _extract_text_from_item(None, "k") == ""


# ── _map_facet_claims ───────────────────────────────────────────────


class TestMapFacetClaims:
    def test_maps_core_theory_to_problem(self):
        axes = {name: CompassAxis(name=name) for name in AXIS_NAMES}
        facets = {
            "core_theory": {
                "claims": [
                    {"claim": "The system values correctness", "confidence": 0.9},
                ]
            }
        }
        _map_facet_claims(facets, axes)
        assert len(axes["problem"].claims) == 1
        assert axes["problem"].claims[0].text == "The system values correctness"
        assert axes["problem"].claims[0].origin_facet == "core_theory"

    def test_skips_empty_claims(self):
        axes = {name: CompassAxis(name=name) for name in AXIS_NAMES}
        facets = {"core_theory": {"claims": [{"claim": "", "confidence": 0.5}]}}
        _map_facet_claims(facets, axes)
        assert len(axes["problem"].claims) == 0

    def test_skips_non_dict_facet(self):
        axes = {name: CompassAxis(name=name) for name in AXIS_NAMES}
        facets = {"core_theory": "not a dict"}
        _map_facet_claims(facets, axes)
        assert len(axes["problem"].claims) == 0

    def test_skips_non_dict_claim_items(self):
        axes = {name: CompassAxis(name=name) for name in AXIS_NAMES}
        facets = {"core_theory": {"claims": ["just a string", 42]}}
        _map_facet_claims(facets, axes)
        assert len(axes["problem"].claims) == 0

    def test_maps_multiple_facets(self):
        axes = {name: CompassAxis(name=name) for name in AXIS_NAMES}
        facets = {
            "core_theory": {"claims": [{"claim": "problem claim"}]},
            "problem_solving": {"claims": [{"claim": "solution claim"}]},
            "abstractions": {"claims": [{"claim": "impl claim"}]},
        }
        _map_facet_claims(facets, axes)
        assert len(axes["problem"].claims) == 1
        assert len(axes["solution"].claims) == 1
        assert len(axes["implementation"].claims) == 1

    def test_uses_text_key_fallback(self):
        axes = {name: CompassAxis(name=name) for name in AXIS_NAMES}
        facets = {"core_theory": {"claims": [{"text": "via text key"}]}}
        _map_facet_claims(facets, axes)
        assert axes["problem"].claims[0].text == "via text key"


# ── _derive_directives ──────────────────────────────────────────────


class TestDeriveDirectives:
    def test_anti_patterns_become_away(self):
        result = {"anti_patterns": [{"pattern": "avoid globals"}]}
        directives = _derive_directives(result, [])
        assert len(directives) == 1
        assert directives[0].kind == "away"
        assert directives[0].text == "avoid globals"

    def test_enforceable_rules_forbid(self):
        result = {"enforceable_rules": [{"pattern": "no eval", "type": "forbid"}]}
        directives = _derive_directives(result, [])
        assert any(d.kind == "forbidden" and "no eval" in d.text for d in directives)

    def test_enforceable_rules_toward(self):
        result = {"enforceable_rules": [{"pattern": "use typing", "type": "require"}]}
        directives = _derive_directives(result, [])
        assert any(d.kind == "toward" for d in directives)

    def test_solution_claims_become_toward(self):
        claims = [
            CompassClaim(text="Use dependency injection", origin_facet="problem_solving"),
        ]
        directives = _derive_directives({}, claims)
        assert len(directives) == 1
        assert directives[0].kind == "toward"
        assert directives[0].source == "solution"

    def test_non_solution_facet_claims_excluded(self):
        claims = [
            CompassClaim(text="Use Python 3", origin_facet="core_theory"),
        ]
        directives = _derive_directives({}, claims)
        assert len(directives) == 0

    def test_string_anti_pattern(self):
        result = {"anti_patterns": ["string pattern"]}
        directives = _derive_directives(result, [])
        assert len(directives) == 1
        assert directives[0].text == "string pattern"


# ── _score_axes ─────────────────────────────────────────────────────


class TestScoreAxes:
    def test_empty_axis_depth_zero(self):
        axes = {"problem": CompassAxis(name="problem")}
        _score_axes(axes)
        assert axes["problem"].depth == 0
        assert axes["problem"].summary == ""

    def test_surface_depth(self):
        axes = {
            "problem": CompassAxis(
                name="problem",
                claims=[CompassClaim(text="simple claim")],
            )
        }
        _score_axes(axes)
        assert axes["problem"].depth == 1
        assert axes["problem"].summary == "simple claim"

    def test_deep_with_causal_markers(self):
        claims = [
            CompassClaim(text="X because Y therefore Z", confidence=1.0),
            CompassClaim(text="A because B", confidence=0.9),
            CompassClaim(text="C therefore D", confidence=0.8),
        ]
        axes = {"problem": CompassAxis(name="problem", claims=claims)}
        _score_axes(axes)
        assert axes["problem"].depth == 3

    def test_summary_picks_highest_confidence(self):
        claims = [
            CompassClaim(text="low conf", confidence=0.3),
            CompassClaim(text="high conf", confidence=0.9),
        ]
        axes = {"solution": CompassAxis(name="solution", claims=claims)}
        _score_axes(axes)
        assert axes["solution"].summary == "high conf"


# ── migrate_from_theory_profile ─────────────────────────────────────


class TestMigrateFromTheoryProfile:
    def test_empty_profile(self):
        state = migrate_from_theory_profile({})
        assert isinstance(state, CompassState)
        assert state.version == 1
        assert all(name in state.axes for name in AXIS_NAMES)
        assert state.forged_at > 0

    def test_full_migration(self):
        profile = {
            "core_theory": {
                "claims": [
                    {"claim": "System does X because Y", "confidence": 0.9},
                    {"claim": "System does A because B", "confidence": 0.8},
                    {"claim": "However system avoids C", "confidence": 0.7},
                    {"claim": "Fourth claim", "confidence": 0.6},
                ]
            },
            "problem_solving": {
                "claims": [
                    {"claim": "Use TDD approach", "confidence": 0.8},
                ]
            },
        }
        full_result = {
            "anti_patterns": [{"pattern": "avoid monkeypatching"}],
            "enforceable_rules": [{"pattern": "no wildcard imports", "type": "forbid"}],
        }
        state = migrate_from_theory_profile(profile, full_result)
        assert len(state.axes["problem"].claims) == 4
        assert len(state.axes["solution"].claims) == 1
        assert state.axes["problem"].depth >= 2
        assert len(state.directives) >= 2
        assert state.gap_report is not None

    def test_non_dict_profile_handled(self):
        state = migrate_from_theory_profile("not a dict")
        assert isinstance(state, CompassState)
        assert state.version == 1

    def test_gap_report_populated(self):
        state = migrate_from_theory_profile({})
        assert isinstance(state.gap_report, GapReport)
        assert "problem" in state.gap_report.axis_depths

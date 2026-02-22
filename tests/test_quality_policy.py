"""Tests for quality policy parsing and dataclasses."""

from __future__ import annotations

from lintgate.types import (
    CoveragePolicy,
    QualityPolicy,
    SecurityPolicy,
    ToleratedFalsePositive,
)


class TestQualityPolicyDataclasses:
    """Verify QualityPolicy and sub-dataclasses."""

    def test_coverage_policy_defaults(self):
        cp = CoveragePolicy()
        assert cp.global_threshold == 80
        assert cp.diff_threshold == 80
        assert cp.source_packages == ["lintgate", "mcp_tools"]

    def test_coverage_policy_custom(self):
        cp = CoveragePolicy(global_threshold=90, diff_threshold=85, source_packages=["mylib"])
        assert cp.global_threshold == 90
        assert cp.diff_threshold == 85
        assert cp.source_packages == ["mylib"]

    def test_tolerated_false_positive(self):
        fp = ToleratedFalsePositive(
            rule="pythonsecurity:S2083",
            file="lintgate/reset.py",
            reason="Read-modify-writeback",
        )
        assert fp.rule == "pythonsecurity:S2083"
        assert fp.file == "lintgate/reset.py"
        assert fp.scope == ""
        assert fp.reason == "Read-modify-writeback"

    def test_tolerated_false_positive_with_scope(self):
        fp = ToleratedFalsePositive(rule="python:S5852", scope="**/*.py")
        assert fp.file == ""
        assert fp.scope == "**/*.py"

    def test_security_policy_defaults(self):
        sp = SecurityPolicy()
        assert sp.tolerated_false_positives == []

    def test_quality_policy_defaults(self):
        qp = QualityPolicy()
        assert qp.coverage.global_threshold == 80
        assert qp.security.tolerated_false_positives == []

    def test_quality_policy_full(self):
        qp = QualityPolicy(
            coverage=CoveragePolicy(global_threshold=85),
            security=SecurityPolicy(
                tolerated_false_positives=[
                    ToleratedFalsePositive(rule="S2083", file="foo.py"),
                ]
            ),
        )
        assert qp.coverage.global_threshold == 85
        assert len(qp.security.tolerated_false_positives) == 1


class TestQualityPolicyYAMLParsing:
    """Verify quality_policy is parsed correctly from lintgate.yaml."""

    def test_parse_full_quality_policy(self, tmp_path):
        """Full quality_policy section parses correctly."""
        yaml_content = """\
quality_policy:
  coverage:
    global_threshold: 85
    diff_threshold: 75
    source_packages:
      - mylib
      - mytools
  security:
    tolerated_false_positives:
      - rule: "pythonsecurity:S2083"
        file: "mylib/reset.py"
        reason: "Local file only"
      - rule: "python:S5852"
        scope: "**/*.py"
        reason: "No user input"
"""
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text(yaml_content)

        from lintgate.config import load_config

        config = load_config(str(tmp_path))
        qp = config.quality_policy

        assert qp.coverage.global_threshold == 85
        assert qp.coverage.diff_threshold == 75
        assert qp.coverage.source_packages == ["mylib", "mytools"]
        assert len(qp.security.tolerated_false_positives) == 2
        assert qp.security.tolerated_false_positives[0].rule == "pythonsecurity:S2083"
        assert qp.security.tolerated_false_positives[0].file == "mylib/reset.py"
        assert qp.security.tolerated_false_positives[1].scope == "**/*.py"

    def test_parse_missing_quality_policy(self, tmp_path):
        """Missing quality_policy section yields defaults."""
        yaml_content = "controlplane:\n  enabled: true\n"
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text(yaml_content)

        from lintgate.config import load_config

        config = load_config(str(tmp_path))
        qp = config.quality_policy

        assert qp.coverage.global_threshold == 80
        assert qp.security.tolerated_false_positives == []

    def test_parse_partial_quality_policy(self, tmp_path):
        """Partial quality_policy merges with defaults."""
        yaml_content = """\
quality_policy:
  coverage:
    global_threshold: 90
"""
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text(yaml_content)

        from lintgate.config import load_config

        config = load_config(str(tmp_path))
        qp = config.quality_policy

        assert qp.coverage.global_threshold == 90
        assert qp.coverage.diff_threshold == 80  # Default
        assert qp.security.tolerated_false_positives == []  # Default

    def test_parse_empty_quality_policy(self, tmp_path):
        """Empty quality_policy section yields defaults."""
        yaml_content = "quality_policy: {}\n"
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text(yaml_content)

        from lintgate.config import load_config

        config = load_config(str(tmp_path))
        qp = config.quality_policy

        assert qp.coverage.global_threshold == 80

    def test_parse_invalid_thresholds_falls_back(self, tmp_path):
        """Invalid threshold values should not crash config load."""
        yaml_content = """\
quality_policy:
  coverage:
    global_threshold: "not-a-number"
    diff_threshold: "still-not-a-number"
    source_packages: "lintgate"
"""
        config_dir = tmp_path / ".claude"
        config_dir.mkdir()
        (config_dir / "lintgate.yaml").write_text(yaml_content)

        from lintgate.config import load_config

        config = load_config(str(tmp_path))
        qp = config.quality_policy

        assert qp.coverage.global_threshold == 80
        assert qp.coverage.diff_threshold == 80
        assert qp.coverage.source_packages == ["lintgate"]

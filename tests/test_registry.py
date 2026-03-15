"""Tests for lintgate/registry.py -- linter registry."""

from __future__ import annotations

from lintgate.registry import _register, _register_custom_linters, build_registry
from lintgate.types import ProjectConfig

# -- build_registry -------------------------------------------------------


def test_build_registry_returns_dict() -> None:
    config = ProjectConfig()
    registry = build_registry(config)
    assert isinstance(registry, dict)


def test_build_registry_includes_ruff_check() -> None:
    config = ProjectConfig()
    registry = build_registry(config)
    assert "ruff_check" in registry


def test_build_registry_includes_ruff_format() -> None:
    config = ProjectConfig()
    registry = build_registry(config)
    assert "ruff_format" in registry


# -- _register ------------------------------------------------------------


def test_register_adds_linter_to_registry() -> None:
    from lintgate.linters.ruff_linter import RuffLinter

    registry: dict = {}
    config = ProjectConfig()
    linter = RuffLinter()
    _register(registry, linter, config)
    assert "ruff_check" in registry


def test_register_skips_disabled_linter() -> None:
    from lintgate.linters.ruff_linter import RuffLinter

    registry: dict = {}
    config = ProjectConfig(enabled_linters={"ruff_check": False})
    linter = RuffLinter()
    _register(registry, linter, config)
    assert "ruff_check" not in registry


def test_register_includes_linter_not_mentioned_in_config() -> None:
    from lintgate.linters.ruff_linter import RuffLinter

    registry: dict = {}
    config = ProjectConfig(enabled_linters={"mypy": True})
    linter = RuffLinter()
    _register(registry, linter, config)
    assert "ruff_check" in registry


# -- _register_custom_linters ---------------------------------------------


def test_register_custom_linters_skips_no_command() -> None:
    registry: dict = {}
    config = ProjectConfig(linter_configs={"some_linter": {"enabled": True}})
    _register_custom_linters(registry, config)
    assert len(registry) == 0


def test_register_custom_linters_adds_with_command() -> None:
    registry: dict = {}
    config = ProjectConfig(
        linter_configs={
            "custom_tool": {
                "enabled": True,
                "command": "python -m custom_tool --json",
                "tier": 3,
            }
        }
    )
    _register_custom_linters(registry, config)
    assert "custom_tool" in registry


def test_register_custom_linters_skips_disabled() -> None:
    registry: dict = {}
    config = ProjectConfig(
        linter_configs={
            "custom_tool": {
                "enabled": False,
                "command": "python -m custom_tool --json",
            }
        }
    )
    _register_custom_linters(registry, config)
    assert "custom_tool" not in registry

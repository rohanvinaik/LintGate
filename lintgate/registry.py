"""Linter registry — discovers and manages available linters.

Built-in linters are always registered. Custom linters can be added
via project config. The registry handles:
- Importing built-in linter classes
- Instantiating custom linters from config
- Filtering by availability (tool installed?)
- Filtering by config (enabled/disabled?)

Custom linters are detected by the presence of a "command" key in
their config block. This lets any project bring its own analysis tools
(TailChasingFixer, ShortcutForge DSL linter, etc.) into the pipeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .linters.base import BaseLinter
    from .types import ProjectConfig


def build_registry(config: ProjectConfig) -> dict[str, BaseLinter]:
    """Build a registry of available linter instances.

    Args:
        config: Project config (for enable/disable overrides)

    Returns:
        Dict mapping linter name -> BaseLinter instance
    """
    registry: dict[str, BaseLinter] = {}

    # ── Built-in linters ──────────────────────────────────────────

    from .linters.ruff_linter import RuffFormatLinter, RuffLinter

    _register(registry, RuffLinter(), config)
    _register(registry, RuffFormatLinter(), config)

    try:
        from .linters.mypy_linter import MypyLinter

        _register(registry, MypyLinter(), config)
    except ImportError:
        pass

    try:
        from .linters.ty_linter import TyLinter

        _register(registry, TyLinter(), config)
    except ImportError:
        pass

    try:
        from .linters.complexity_checker import ComplexityChecker

        _register(registry, ComplexityChecker(), config)
    except ImportError:
        pass

    try:
        from .linters.import_checker import ImportChecker

        _register(registry, ImportChecker(), config)
    except ImportError:
        pass

    try:
        from .linters.version_checker import VersionChecker

        _register(registry, VersionChecker(), config)
    except ImportError:
        pass

    try:
        from .linters.bandit_linter import BanditLinter

        _register(registry, BanditLinter(), config)
    except ImportError:
        pass

    try:
        from .linters.bandit_fast_linter import BanditFastLinter

        _register(registry, BanditFastLinter(), config)
    except ImportError:
        pass

    try:
        from .linters.pip_audit_linter import PipAuditLinter

        _register(registry, PipAuditLinter(), config)
    except ImportError:
        pass

    # ── Clean code linters (no external deps, always available) ────

    try:
        from .linters.structure_checker import StructureChecker

        _register(registry, StructureChecker(), config)
    except ImportError:
        pass

    try:
        from .linters.context_rule_checker import ContextRuleChecker

        _register(registry, ContextRuleChecker(), config)
    except ImportError:
        pass

    try:
        from .linters.redefinition_checker import RedefinitionChecker

        _register(registry, RedefinitionChecker(), config)
    except ImportError:
        pass

    try:
        from .linters.architecture_checker import ArchitectureChecker

        _register(registry, ArchitectureChecker(), config)
    except ImportError:
        pass

    try:
        from .linters.dead_code_checker import DeadCodeChecker

        _register(registry, DeadCodeChecker(), config)
    except ImportError:
        pass

    try:
        from .linters.performance_checker import PerformanceChecker

        _register(registry, PerformanceChecker(), config)
    except ImportError:
        pass

    # ── Custom linters from config ────────────────────────────────
    # A linter config entry with a "command" key is treated as a custom
    # linter. This is the bridge for project-specific tools like
    # TailChasingFixer or ShortcutForge's DSL linter.

    _register_custom_linters(registry, config)

    return registry


def _register_custom_linters(
    registry: dict[str, BaseLinter],
    config: ProjectConfig,
) -> None:
    """Instantiate and register custom linters from config.

    Custom linters are identified by having a "command" key in their
    config block. Example lintgate.yaml:

        linters:
          custom_tailchasing:
            enabled: true
            command: "python -m tailchasing.cli --json src/"
            tier: 3
            severity_default: "warning"
            parse_mode: "jsonl"
    """
    try:
        from .linters.custom_linter import CustomLinter
    except ImportError:
        return

    for name, linter_conf in config.linter_configs.items():
        if not isinstance(linter_conf, dict):
            continue

        # Skip if no command — it's a built-in linter config, not custom
        command = linter_conf.get("command")
        if not command:
            continue

        # Skip if explicitly disabled
        if not linter_conf.get("enabled", True):
            continue

        # Skip if already registered (built-in takes precedence)
        if name in registry:
            continue

        custom = CustomLinter(
            linter_name=name,
            command=command,
            tier=linter_conf.get("tier", 3),
            severity_default=linter_conf.get("severity_default", "warning"),
            parse_mode=linter_conf.get("parse_mode", "lines"),
            timeout_ms=linter_conf.get("timeout_ms", 15000),
        )

        registry[name] = custom


def _register(
    registry: dict[str, BaseLinter],
    linter: BaseLinter,
    config: ProjectConfig,
) -> None:
    """Register a linter if it's enabled in config.

    If config doesn't mention the linter, it's enabled by default.
    """
    # Check if explicitly disabled in config
    if linter.name in config.enabled_linters and not config.enabled_linters[linter.name]:
        return  # Explicitly disabled

    registry[linter.name] = linter

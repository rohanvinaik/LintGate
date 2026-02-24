"""Provider-agnostic command-file profile validation and auto-remediation.

Some agent CLIs load local command files from provider-specific directories.
When those files drift from the provider's accepted schema, agent startup can
fail before the MCP server is even reachable.

This module enforces provider command profiles during setup/doctor flows:
- Pre-generate canonical template files for known providers.
- Validate local command files against profile schema constraints.
- Auto-migrate legacy files into canonical schema with backups.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib


@dataclass(frozen=True)
class CommandTemplate:
    file_name: str
    prompt: str
    description: str


@dataclass(frozen=True)
class CommandProfile:
    id: str
    commands_dir: Path
    templates: tuple[CommandTemplate, ...]
    required_string_keys: tuple[str, ...] = ("prompt",)
    optional_string_keys: tuple[str, ...] = ("description",)
    strict_keys: bool = True


def _candidate_with_suffix(path: Path, suffix: str) -> Path:
    candidate = Path(f"{path}{suffix}")
    counter = 1
    while candidate.exists():
        candidate = Path(f"{path}{suffix}.{counter}")
        counter += 1
    return candidate


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _render_command_toml(prompt: str, description: str) -> str:
    lines = [
        "# Auto-managed by lintgate-admin command profile sync.",
        f"prompt = {json.dumps(prompt)}",
        f"description = {json.dumps(description)}",
        "",
    ]
    return "\n".join(lines)


def _legacy_prompt(parsed: dict[str, Any], file_name: str, raw_text: str) -> tuple[str, str]:
    parts: list[str] = []

    system = parsed.get("system_instructions")
    if isinstance(system, dict):
        role = _as_text(system.get("role"))
        goal = _as_text(system.get("goal"))
        constraints = _as_text(system.get("constraints"))
        if role:
            parts.append(f"Role: {role}")
        if goal:
            parts.append(f"Goal: {goal}")
        if constraints:
            parts.append(f"Constraints: {constraints}")

    project = parsed.get("project")
    project_name = ""
    if isinstance(project, dict):
        project_name = _as_text(project.get("name"))
        project_type = _as_text(project.get("type"))
        objective = _as_text(project.get("objective"))
        if project_name:
            parts.append(f"Project: {project_name}")
        if project_type:
            parts.append(f"Project type: {project_type}")
        if objective:
            parts.append(f"Objective: {objective}")

    framework = parsed.get("framework")
    if isinstance(framework, dict):
        framework_name = _as_text(framework.get("name"))
        if framework_name:
            parts.append(f"Framework: {framework_name}")

    steps = parsed.get("steps")
    if isinstance(steps, list):
        step_lines: list[str] = []
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            label = (
                _as_text(step.get("name"))
                or _as_text(step.get("title"))
                or _as_text(step.get("action"))
                or _as_text(step.get("description"))
            )
            if label:
                step_lines.append(f"{idx}. {label}")
        if step_lines:
            parts.append("Plan:\n" + "\n".join(step_lines))

    output = parsed.get("output")
    if isinstance(output, dict):
        fmt = _as_text(output.get("format"))
        if fmt:
            parts.append(f"Output format: {fmt}")

    if not parts:
        keys = ", ".join(sorted(str(k) for k in parsed))
        preview = raw_text.strip().replace("\n", " ")[:240]
        parts.append(
            "Migrate and execute this legacy command definition safely. "
            f"Legacy keys: {keys or 'none'}. Legacy preview: {preview}"
        )

    prompt = "\n".join(parts)
    description_base = project_name or Path(file_name).stem
    description = f"Migrated command profile file: {description_base}"
    return prompt, description


def _recover_prompt(file_name: str, raw_text: str) -> tuple[str, str]:
    preview_lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    preview = "\n".join(preview_lines[:24])[:1200]
    prompt = (
        "This command file was auto-recovered from invalid TOML.\n"
        "Infer the intended workflow from the legacy content below and execute conservatively.\n"
        f"Legacy content preview:\n{preview or '(empty file)'}"
    )
    description = f"Recovered command profile file: {Path(file_name).stem}"
    return prompt, description


def _is_valid_schema(parsed: Any, profile: CommandProfile) -> bool:
    if not isinstance(parsed, dict):
        return False

    allowed = set(profile.required_string_keys) | set(profile.optional_string_keys)
    if profile.strict_keys and any(key not in allowed for key in parsed):
        return False

    for key in profile.required_string_keys:
        value = parsed.get(key)
        if not isinstance(value, str) or not value.strip():
            return False

    for key in profile.optional_string_keys:
        value = parsed.get(key)
        if value is not None and not isinstance(value, str):
            return False

    return True


def _default_command_profiles() -> dict[str, CommandProfile]:
    # Keep all command profile constraints in-repo so setup can pre-generate
    # canonical templates deterministically.
    return {
        "antigravity": CommandProfile(
            id="antigravity",
            commands_dir=Path(os.path.expanduser("~/.gemini/commands")),
            templates=(
                CommandTemplate(
                    file_name="plan.toml",
                    description="Generate an implementation plan before coding.",
                    prompt=(
                        "Generate a concise implementation plan for the current task.\n"
                        "Include: scope, risks, sequence, validation checks, and rollback strategy."
                    ),
                ),
            ),
            required_string_keys=("prompt",),
            optional_string_keys=("description",),
            strict_keys=True,
        ),
    }


def sync_agent_command_profile(
    agent_id: str,
    apply: bool = True,
    *,
    allow_create_dirs: bool = True,
) -> dict[str, Any] | None:
    """Validate and optionally repair local command files for an agent profile."""

    profile = _default_command_profiles().get(agent_id.lower())
    if profile is None:
        return None

    report: dict[str, Any] = {
        "profile": profile.id,
        "commands_dir": str(profile.commands_dir),
        "exists": profile.commands_dir.exists(),
        "apply": apply,
        "scanned": 0,
        "blocking_issues": 0,
        "summary": {
            "valid": 0,
            "migrated": 0,
            "recovered": 0,
            "generated_templates": 0,
            "errors": 0,
        },
        "entries": [],
    }

    if apply and allow_create_dirs:
        profile.commands_dir.mkdir(parents=True, exist_ok=True)
        report["exists"] = True
    if apply and profile.commands_dir.exists():
        for template in profile.templates:
            template_path = profile.commands_dir / template.file_name
            if template_path.exists():
                continue
            try:
                template_path.write_text(
                    _render_command_toml(template.prompt, template.description),
                    encoding="utf-8",
                )
            except OSError as exc:
                report["entries"].append(
                    {
                        "file": str(template_path),
                        "status": "error",
                        "reason": f"template_write_error:{exc}",
                    }
                )
                report["summary"]["errors"] += 1
                report["blocking_issues"] += 1
            else:
                report["entries"].append({"file": str(template_path), "status": "generated_template"})
                report["summary"]["generated_templates"] += 1

    if not profile.commands_dir.exists():
        return report

    for file_path in sorted(profile.commands_dir.glob("*.toml")):
        report["scanned"] += 1
        entry: dict[str, Any] = {"file": str(file_path)}
        report["entries"].append(entry)

        try:
            raw_text = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            entry["status"] = "error"
            entry["reason"] = f"read_error:{exc}"
            report["summary"]["errors"] += 1
            report["blocking_issues"] += 1
            continue

        try:
            parsed = tomllib.loads(raw_text)
        except tomllib.TOMLDecodeError as exc:
            if not apply:
                entry["status"] = "invalid_toml"
                entry["reason"] = str(exc)
                report["blocking_issues"] += 1
                continue

            prompt, description = _recover_prompt(file_path.name, raw_text)
            backup_path = _candidate_with_suffix(file_path, ".bak")
            try:
                file_path.rename(backup_path)
                file_path.write_text(_render_command_toml(prompt, description), encoding="utf-8")
            except OSError as write_exc:
                entry["status"] = "error"
                entry["reason"] = f"recover_error:{write_exc}"
                report["summary"]["errors"] += 1
                report["blocking_issues"] += 1
                continue

            entry["status"] = "recovered"
            entry["backup"] = str(backup_path)
            report["summary"]["recovered"] += 1
            continue

        if _is_valid_schema(parsed, profile):
            entry["status"] = "valid"
            report["summary"]["valid"] += 1
            continue

        if not isinstance(parsed, dict):
            entry["status"] = "invalid_schema"
            entry["reason"] = "top-level TOML value must be an object"
            report["blocking_issues"] += 1
            continue

        if not apply:
            entry["status"] = "needs_migration"
            entry["reason"] = "file does not match command profile schema"
            report["blocking_issues"] += 1
            continue

        prompt, description = _legacy_prompt(parsed, file_path.name, raw_text)
        backup_path = _candidate_with_suffix(file_path, ".bak")
        try:
            file_path.rename(backup_path)
            file_path.write_text(_render_command_toml(prompt, description), encoding="utf-8")
        except OSError as exc:
            entry["status"] = "error"
            entry["reason"] = f"migrate_error:{exc}"
            report["summary"]["errors"] += 1
            report["blocking_issues"] += 1
            continue

        entry["status"] = "migrated"
        entry["backup"] = str(backup_path)
        report["summary"]["migrated"] += 1

    return report


def sync_all_command_profiles(
    apply: bool = True,
    *,
    allow_create_dirs: bool = True,
) -> list[dict[str, Any]]:
    """Sync all known command profiles.

    Useful at MCP server startup to self-heal local command profile drift.
    """

    reports: list[dict[str, Any]] = []
    for profile_id in sorted(_default_command_profiles()):
        report = sync_agent_command_profile(
            profile_id,
            apply=apply,
            allow_create_dirs=allow_create_dirs,
        )
        if report is not None:
            reports.append(report)
    return reports

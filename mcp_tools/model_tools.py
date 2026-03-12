"""Model calibration tools — model_profile_status, model_profile_probe_start, model_profile_probe_submit."""

from __future__ import annotations


def register(mcp, helpers):
    """Register model calibration tools on the shared MCP instance."""

    @mcp.tool()
    def model_profile_status(
        path: str,
        model_id: str | None = None,
    ) -> str:
        """Show model calibration profile status.

        Returns the resolved model key, calibration status, signal risk vector,
        and confidence level. If model_id is None, returns all stored profiles.

        Args:
            path: Project root path.
            model_id: Optional model identifier (e.g., "claude-opus-4", "gpt-4o").
                If None, returns summary of all stored profiles.
        """
        from lintgate.controlplane.model.profiles import (
            ModelProfile,
            load_profiles,
            resolve_model_key,
        )

        store = load_profiles()

        if model_id is None:
            # Summary of all stored profiles
            summaries = []
            for key, prof in store.profiles.items():
                status = (
                    "usable"
                    if prof.is_usable()
                    else ("stale" if prof.is_stale() else "low_confidence")
                )
                summaries.append(
                    {
                        "model_key": key,
                        "status": status,
                        "confidence": prof.confidence,
                        "probe_version": prof.probe_version,
                        "probe_runs": prof.probe_runs,
                        "telemetry_samples": prof.telemetry_samples,
                        "signal_count": len(prof.signal_risk),
                        "age_days": round((__import__("time").time() - prof.updated_at) / 86400, 1),
                    }
                )
            return helpers["_json_dumps"](  # type: ignore[no-any-return]
                {
                    "profiles_count": len(summaries),
                    "profiles": summaries,
                    "next_actions": [
                        "model_profile_probe_start(model_id='<model>') — calibrate a new model",
                    ]
                    if not summaries
                    else [
                        "model_profile_probe_start(model_id='<model>') — "
                        "calibrate or recalibrate a model",
                        "bootstrap_context_files(model_id='<model>') — "
                        "generate model-aware bootstrap content",
                    ],
                }
            )

        # Specific model lookup
        canonical = resolve_model_key(model_id)
        if canonical is None:
            return helpers["_json_dumps"](  # type: ignore[no-any-return]
                {
                    "model_id": model_id,
                    "status": "unresolved",
                    "message": (
                        f"Cannot resolve model identifier {model_id!r}. "
                        "Expected format: 'claude-opus-4', 'gpt-4o', "
                        "or 'provider:model-name'."
                    ),
                    "next_actions": [
                        "Provide a recognized model identifier.",
                    ],
                }
            )

        profile: ModelProfile | None = store.profiles.get(canonical)
        if profile is None:
            return helpers["_json_dumps"](  # type: ignore[no-any-return]
                {
                    "model_key": canonical,
                    "status": "no_profile",
                    "message": f"No calibration profile found for {canonical}.",
                    "next_actions": [
                        f"model_profile_probe_start(model_id='{model_id}') — "
                        "run calibration probe (60-120 seconds)",
                    ],
                }
            )

        import time as _time

        from lintgate.controlplane.model.profiles import apply_confidence_decay

        # Apply decay and capture original for display
        confidence_raw = apply_confidence_decay(profile)
        age_days = round((_time.time() - profile.updated_at) / 86400, 1)

        status = (
            "usable"
            if profile.is_usable()
            else ("stale" if profile.is_stale() else "low_confidence")
        )

        result: dict = {
            "model_key": canonical,
            "status": status,
            "confidence": profile.confidence,
            "confidence_raw": confidence_raw,
            "days_since_update": age_days,
            "probe_version": profile.probe_version,
            "probe_runs": profile.probe_runs,
            "telemetry_samples": profile.telemetry_samples,
            "age_days": age_days,
            "signal_risk": profile.signal_risk,
            "custom_anti_patterns_count": len(profile.custom_anti_patterns),
            "custom_dispositions_count": len(profile.custom_dispositions),
        }

        next_actions = []
        if status == "stale":
            next_actions.append(
                f"model_profile_probe_start(model_id='{model_id}') — recalibrate (profile is stale)"
            )
        elif status == "low_confidence":
            next_actions.append(
                f"model_profile_probe_start(model_id='{model_id}') — recalibrate (low confidence)"
            )
        if status == "usable":
            next_actions.append(
                f"bootstrap_context_files(model_id='{model_id}') — "
                "generate model-aware bootstrap content"
            )
        result["next_actions"] = next_actions
        return helpers["_json_dumps"](result)  # type: ignore[no-any-return]

    @mcp.tool()
    def model_profile_probe_start(
        path: str,
        model_id: str,
        probe_set: str = "quick",
    ) -> str:
        """Start a model calibration probe.

        Returns 5 behavioral micro-tasks that reveal the model's actual
        coding tendencies (approach cycling, verification habits, etc.).
        Complete each task and submit responses via model_profile_probe_submit.

        Each task presents a small coding scenario. Respond with both a
        text description of your approach AND structured trace fields
        (tool_calls, actions, verify_points) for best calibration accuracy.

        Args:
            path: Project root path.
            model_id: Model identifier (e.g., "claude-opus-4", "gpt-4o").
            probe_set: Probe task set. Currently only "quick" (5 tasks).
        """
        from lintgate.controlplane.model.probe import (
            PROBE_VERSION,
            SUPPORTED_PROBE_SETS,
            get_probe_tasks,
        )
        from lintgate.controlplane.model.profiles import (
            resolve_model_key,
        )

        canonical = resolve_model_key(model_id)
        if canonical is None:
            return helpers["_json_dumps"](  # type: ignore[no-any-return]
                {
                    "error": f"Cannot resolve model identifier {model_id!r}.",
                    "hint": (
                        "Expected format: 'claude-opus-4', 'gpt-4o', or 'provider:model-name'."
                    ),
                }
            )

        try:
            tasks = get_probe_tasks(probe_set)
        except ValueError as e:
            return helpers["_json_dumps"](  # type: ignore[no-any-return]
                {
                    "error": str(e),
                    "supported_probe_sets": sorted(SUPPORTED_PROBE_SETS),
                }
            )
        probe_set = probe_set.strip().lower()

        # Check for existing profile
        from lintgate.controlplane.model.profiles import get_profile

        existing = get_profile(model_id)
        existing_info = None
        if existing is not None:
            import time as _time

            existing_info = {
                "confidence": existing.confidence,
                "probe_version": existing.probe_version,
                "probe_runs": existing.probe_runs,
                "age_days": round((_time.time() - existing.updated_at) / 86400, 1),
                "status": "usable"
                if existing.is_usable()
                else ("stale" if existing.is_stale() else "low_confidence"),
            }

        return helpers["_json_dumps"](  # type: ignore[no-any-return]
            {
                "model_key": canonical,
                "probe_version": f"v{PROBE_VERSION}",
                "probe_set": probe_set,
                "task_count": len(tasks),
                "tasks": tasks,
                "response_schema": {
                    "format": {
                        "task_id": {
                            "text": "str (required): Describe your approach",
                            "tool_calls": "list[str] (optional): Ordered tool names",
                            "actions": "list[str] (optional): Ordered action descriptions",
                            "retry_count": "int (optional): Times same command retried",
                            "verify_points": "list[int] (optional): After which steps you verify",
                            "constraint_refs": "list[str] (optional): Errors/constraints referenced",
                        },
                    },
                    "example": {
                        tasks[0]["id"]: {
                            "text": "I would first read the source file to understand the code...",
                            "tool_calls": ["Read", "Read", "Edit", "Bash"],
                            "actions": [
                                "Read utils.py to understand function",
                                "Read test output carefully",
                                "Fix the shadowed variable on line 5",
                                "Run pytest to verify",
                            ],
                            "verify_points": [3],
                            "constraint_refs": ["variable shadowing in loop"],
                        },
                    },
                    "minimum_tasks": 3,
                    "note": (
                        "Structured trace fields (tool_calls, actions, verify_points) "
                        "significantly improve calibration accuracy. Text-only responses "
                        "are accepted but produce lower-confidence profiles."
                    ),
                },
                "existing_profile": existing_info,
                "eta": "60-120 seconds",
                "next_actions": [
                    "Complete each task, then call "
                    f"model_profile_probe_submit(model_id='{model_id}', "
                    "answers={{task_id: {{text: '...', tool_calls: [...], ...}}}})",
                ],
            }
        )

    @mcp.tool()
    def model_profile_probe_submit(
        path: str,
        model_id: str,
        answers: dict[str, str] | None = None,
        probe_version: str = "v2",
    ) -> str:
        """Submit answers to a model calibration probe.

        Scores the responses deterministically into a signal_risk vector,
        derives model-specific anti-patterns and guardrail dispositions,
        and persists the profile for future bootstrap use.

        Args:
            path: Project root path.
            model_id: Model identifier (e.g., "claude-opus-4").
            answers: Task responses as {task_id: response_dict}.
                Minimum 3 answers required. Example:
                {"t1_error_reading": {"text": "I would first read...", "tool_calls": ["Read", "Edit"]}}
            probe_version: Probe version string (default "v2").
        """
        from lintgate.controlplane.model.probe import (
            PROBE_TASKS,
            PROBE_VERSION,
            build_profile_from_probe,
        )
        from lintgate.controlplane.model.profiles import (
            get_profile,
            resolve_model_key,
            upsert_profile,
        )

        # Validate probe version
        expected_version = f"v{PROBE_VERSION}"
        if probe_version == "v1":
            return helpers["_json_dumps"](  # type: ignore[no-any-return]
                {
                    "error": "Probe v1 is no longer supported.",
                    "current_version": expected_version,
                    "hint": (
                        "Run model_profile_probe_start to get v2 micro-task probes. "
                        "v2 uses behavioral micro-tasks instead of multiple-choice questions."
                    ),
                    "next_actions": [
                        f"model_profile_probe_start(model_id='{model_id}') — get v2 tasks",
                    ],
                }
            )
        if probe_version != expected_version:
            return helpers["_json_dumps"](  # type: ignore[no-any-return]
                {
                    "error": f"Unknown probe version: {probe_version!r}",
                    "expected": expected_version,
                    "hint": "Run model_profile_probe_start to get current tasks.",
                }
            )

        # Validate model key
        canonical = resolve_model_key(model_id)
        if canonical is None:
            return helpers["_json_dumps"](  # type: ignore[no-any-return]
                {
                    "error": f"Cannot resolve model identifier {model_id!r}.",
                }
            )

        # Validate answers
        if not answers:
            return helpers["_json_dumps"](  # type: ignore[no-any-return]
                {
                    "error": "No answers provided.",
                    "hint": "Provide answers as {task_id: {text: '...', tool_calls: [...], ...}}.",
                }
            )

        # Normalize answers: if value is a string, wrap in {text: value}
        # This handles v1-style answers and bare-text responses gracefully
        normalized_answers: dict = {}
        for task_id, answer in answers.items():
            if isinstance(answer, str):
                normalized_answers[task_id] = {"text": answer}
            elif isinstance(answer, dict):
                normalized_answers[task_id] = answer
            else:
                normalized_answers[task_id] = {"text": str(answer)}

        valid_ids = {t.id for t in PROBE_TASKS}
        invalid_ids = set(normalized_answers.keys()) - valid_ids
        if invalid_ids:
            return helpers["_json_dumps"](  # type: ignore[no-any-return]
                {
                    "error": f"Unknown task IDs: {sorted(invalid_ids)}",
                    "valid_ids": sorted(valid_ids),
                }
            )

        if len(normalized_answers) < 3:
            return helpers["_json_dumps"](  # type: ignore[no-any-return]
                {
                    "error": (
                        f"Minimum 3 task responses required, got {len(normalized_answers)}. "
                        "Complete more tasks for a usable profile."
                    ),
                }
            )

        # Score and build profile
        try:
            profile = build_profile_from_probe(model_id, normalized_answers)
        except ValueError as e:
            return helpers["_json_dumps"]({"error": str(e)})  # type: ignore[no-any-return]

        # Preserve run history on recalibration.
        existing = get_profile(model_id)
        if existing is not None:
            profile.created_at = existing.created_at
            profile.probe_runs = max(existing.probe_runs, 1) + 1
            profile.stale_after_days = existing.stale_after_days

        # Persist
        upsert_profile(profile)

        # Telemetry: track model_calibration usage
        import contextlib

        with contextlib.suppress(Exception):
            from lintgate.state import log_feature_usage

            log_feature_usage("model_calibration", helpers["_validate_project_root"](path))

        status = "usable" if profile.is_usable() else "low_confidence"

        next_actions = []
        if status == "usable":
            next_actions.append(
                f"bootstrap_context_files(model_id='{model_id}') — "
                "generate model-aware bootstrap content"
            )
        else:
            next_actions.append("Complete more tasks to increase confidence above 0.55 threshold.")
        next_actions.append(
            f"model_profile_status(model_id='{model_id}') — view full profile details"
        )

        return helpers["_json_dumps"](  # type: ignore[no-any-return]
            {
                "model_key": canonical,
                "status": status,
                "confidence": profile.confidence,
                "probe_version": profile.probe_version,
                "probe_runs": profile.probe_runs,
                "signal_risk": profile.signal_risk,
                "custom_anti_patterns": profile.custom_anti_patterns,
                "custom_dispositions": profile.custom_dispositions,
                "tasks_scored": len(normalized_answers),
                "message": (
                    f"Profile created for {canonical} with confidence {profile.confidence:.2f}. "
                    f"Probe v{profile.probe_version} uses behavioral micro-tasks "
                    f"(weak prior, decays fast as real telemetry arrives)."
                ),
                "next_actions": next_actions,
            }
        )

    return {
        "model_profile_status": model_profile_status,
        "model_profile_probe_start": model_profile_probe_start,
        "model_profile_probe_submit": model_profile_probe_submit,
    }

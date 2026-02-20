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
        from lintgate.controlplane.model_profiles import (
            load_profiles,
            resolve_model_key,
        )

        store = load_profiles()

        if model_id is None:
            # Summary of all stored profiles
            summaries = []
            for key, profile in store.profiles.items():
                status = (
                    "usable"
                    if profile.is_usable()
                    else ("stale" if profile.is_stale() else "low_confidence")
                )
                summaries.append(
                    {
                        "model_key": key,
                        "status": status,
                        "confidence": profile.confidence,
                        "probe_runs": profile.probe_runs,
                        "telemetry_samples": profile.telemetry_samples,
                        "signal_count": len(profile.signal_risk),
                        "age_days": round(
                            (__import__("time").time() - profile.updated_at) / 86400, 1
                        ),
                    }
                )
            return helpers["_json_dumps"](
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
            return helpers["_json_dumps"](
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

        profile = store.profiles.get(canonical)
        if profile is None:
            return helpers["_json_dumps"](
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

        status = (
            "usable"
            if profile.is_usable()
            else ("stale" if profile.is_stale() else "low_confidence")
        )
        age_days = round((_time.time() - profile.updated_at) / 86400, 1)

        result: dict = {
            "model_key": canonical,
            "status": status,
            "confidence": profile.confidence,
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
        return helpers["_json_dumps"](result)

    @mcp.tool()
    def model_profile_probe_start(
        path: str,
        model_id: str,
        probe_set: str = "quick",
    ) -> str:
        """Start a model calibration probe.

        Returns 5 multiple-choice questions that reveal the model's behavioral
        tendencies (approach cycling, verification habits, etc.). Answer all
        questions and submit via model_profile_probe_submit.

        Args:
            path: Project root path.
            model_id: Model identifier (e.g., "claude-opus-4", "gpt-4o").
            probe_set: Probe question set. Currently only "quick" (5 questions).
        """
        from lintgate.controlplane.model_probe import (
            PROBE_VERSION,
            SUPPORTED_PROBE_SETS,
            get_probe_questions,
        )
        from lintgate.controlplane.model_profiles import (
            resolve_model_key,
        )

        canonical = resolve_model_key(model_id)
        if canonical is None:
            return helpers["_json_dumps"](
                {
                    "error": f"Cannot resolve model identifier {model_id!r}.",
                    "hint": (
                        "Expected format: 'claude-opus-4', 'gpt-4o', or 'provider:model-name'."
                    ),
                }
            )

        try:
            questions = get_probe_questions(probe_set)
        except ValueError as e:
            return helpers["_json_dumps"](
                {
                    "error": str(e),
                    "supported_probe_sets": sorted(SUPPORTED_PROBE_SETS),
                }
            )
        probe_set = probe_set.strip().lower()

        # Check for existing profile
        from lintgate.controlplane.model_profiles import get_profile

        existing = get_profile(model_id)
        existing_info = None
        if existing is not None:
            import time as _time

            existing_info = {
                "confidence": existing.confidence,
                "probe_runs": existing.probe_runs,
                "age_days": round((_time.time() - existing.updated_at) / 86400, 1),
                "status": "usable"
                if existing.is_usable()
                else ("stale" if existing.is_stale() else "low_confidence"),
            }

        return helpers["_json_dumps"](
            {
                "model_key": canonical,
                "probe_version": f"v{PROBE_VERSION}",
                "probe_set": probe_set,
                "question_count": len(questions),
                "questions": questions,
                "answer_schema": {
                    "format": {"question_id": "choice_letter"},
                    "example": {questions[0]["id"]: "B"},
                    "minimum_answers": 3,
                },
                "existing_profile": existing_info,
                "eta": "60-120 seconds",
                "next_actions": [
                    "Answer each question with a letter (A-D), then call "
                    f"model_profile_probe_submit(model_id='{model_id}', "
                    "answers={{...}})",
                ],
            }
        )

    @mcp.tool()
    def model_profile_probe_submit(
        path: str,
        model_id: str,
        answers: dict[str, str] | None = None,
        probe_version: str = "v1",
    ) -> str:
        """Submit answers to a model calibration probe.

        Scores the responses deterministically into a signal_risk vector,
        derives model-specific anti-patterns and guardrail dispositions,
        and persists the profile for future bootstrap use.

        Args:
            path: Project root path.
            model_id: Model identifier (e.g., "claude-opus-4").
            answers: Question responses as {question_id: choice_letter}.
                Minimum 3 answers required. Example:
                {"q1_failure_response": "B", "q2_verification_habits": "A"}
            probe_version: Probe version string (default "v1").
        """
        from lintgate.controlplane.model_probe import (
            PROBE_VERSION,
            build_profile_from_probe,
            get_probe_questions,
        )
        from lintgate.controlplane.model_profiles import (
            get_profile,
            resolve_model_key,
            upsert_profile,
        )

        # Validate probe version
        expected_version = f"v{PROBE_VERSION}"
        if probe_version != expected_version:
            return helpers["_json_dumps"](
                {
                    "error": f"Unknown probe version: {probe_version!r}",
                    "expected": expected_version,
                    "hint": "Run model_profile_probe_start to get current questions.",
                }
            )

        # Validate model key
        canonical = resolve_model_key(model_id)
        if canonical is None:
            return helpers["_json_dumps"](
                {
                    "error": f"Cannot resolve model identifier {model_id!r}.",
                }
            )

        # Validate answers
        if not answers:
            return helpers["_json_dumps"](
                {
                    "error": "No answers provided.",
                    "hint": "Provide answers as {question_id: choice_letter}.",
                }
            )

        valid_ids = {q["id"] for q in get_probe_questions()}
        invalid_ids = set(answers.keys()) - valid_ids
        if invalid_ids:
            return helpers["_json_dumps"](
                {
                    "error": f"Unknown question IDs: {sorted(invalid_ids)}",
                    "valid_ids": sorted(valid_ids),
                }
            )

        if len(answers) < 3:
            return helpers["_json_dumps"](
                {
                    "error": (
                        f"Minimum 3 answers required, got {len(answers)}. "
                        "Answer more questions for a usable profile."
                    ),
                }
            )

        # Score and build profile
        try:
            profile = build_profile_from_probe(model_id, answers)
        except ValueError as e:
            return helpers["_json_dumps"]({"error": str(e)})

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
            next_actions.append(
                "Answer more questions to increase confidence above 0.55 threshold."
            )
        next_actions.append(
            f"model_profile_status(model_id='{model_id}') — view full profile details"
        )

        return helpers["_json_dumps"](
            {
                "model_key": canonical,
                "status": status,
                "confidence": profile.confidence,
                "probe_runs": profile.probe_runs,
                "signal_risk": profile.signal_risk,
                "custom_anti_patterns": profile.custom_anti_patterns,
                "custom_dispositions": profile.custom_dispositions,
                "answers_submitted": len(answers),
                "message": (
                    f"Profile created for {canonical} with confidence {profile.confidence:.2f}."
                ),
                "next_actions": next_actions,
            }
        )

    return {
        "model_profile_status": model_profile_status,
        "model_profile_probe_start": model_profile_probe_start,
        "model_profile_probe_submit": model_profile_probe_submit,
    }

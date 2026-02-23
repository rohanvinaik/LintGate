"""Tests for lintgate/controlplane/command_normalization.py — full symbol coverage."""

from __future__ import annotations

from lintgate.controlplane.command_normalization import (
    _ABS_PATH_PATTERN,
    _EXIT_CODE_LINE,
    _SECRET_PATTERN,
    _TOOL_TYPE_DEFAULTS,
    _WRAPPER_PREFIXES,
    DEFAULT_INTENT_MAP,
    DEFAULT_INTENT_SIG_MAP,
    INTENT_CATEGORIES,
    _extract_first_positional_arg,
    _strip_wrapper_prefixes,
    error_memory_key,
    extract_error_sig,
    normalize_command_sig,
    resolve_intent,
)

# ── Constants sanity checks ─────────────────────────────────────────────


class TestConstants:
    """Verify module-level constants are well-formed."""

    def test_intent_categories_is_frozenset(self) -> None:
        assert isinstance(INTENT_CATEGORIES, frozenset)
        assert "unknown" in INTENT_CATEGORIES
        assert len(INTENT_CATEGORIES) == 6

    def test_tool_type_defaults_cover_known_tools(self) -> None:
        assert _TOOL_TYPE_DEFAULTS["Read"] == "inspect"
        assert _TOOL_TYPE_DEFAULTS["Write"] == "modify"
        assert _TOOL_TYPE_DEFAULTS["Edit"] == "modify"
        assert _TOOL_TYPE_DEFAULTS["Task"] == "meta"

    def test_default_intent_map_values_in_categories(self) -> None:
        for binary, intent in DEFAULT_INTENT_MAP.items():
            assert intent in INTENT_CATEGORIES, f"{binary} -> {intent} not in categories"

    def test_default_intent_sig_map_values_in_categories(self) -> None:
        for sig, intent in DEFAULT_INTENT_SIG_MAP.items():
            assert intent in INTENT_CATEGORIES, f"{sig} -> {intent} not in categories"

    def test_wrapper_prefixes_are_tuples(self) -> None:
        for prefix in _WRAPPER_PREFIXES:
            assert isinstance(prefix, tuple)
            assert len(prefix) >= 1


# ── resolve_intent ──────────────────────────────────────────────────────


class TestResolveIntent:
    """Tests for resolve_intent."""

    def test_non_bash_tool_uses_tool_type_defaults(self) -> None:
        assert resolve_intent("Read", "") == "inspect"
        assert resolve_intent("Write", "") == "modify"
        assert resolve_intent("Grep", "anything") == "inspect"

    def test_unknown_non_bash_tool_returns_unknown(self) -> None:
        assert resolve_intent("SomeNewTool", "") == "unknown"

    def test_bash_exact_sig_match(self) -> None:
        assert resolve_intent("Bash", "git:status") == "inspect"
        assert resolve_intent("Bash", "git:add") == "modify"
        assert resolve_intent("Bash", "git:push") == "execute"

    def test_bash_binary_wildcard_match(self) -> None:
        assert resolve_intent("Bash", "pytest:tests") == "verify"
        assert resolve_intent("Bash", "mkdir:foo") == "modify"
        assert resolve_intent("Bash", "curl:example") == "execute"

    def test_bash_fallback_to_execute(self) -> None:
        assert resolve_intent("Bash", "some_obscure_binary:arg") == "execute"

    def test_bash_empty_sig_returns_execute(self) -> None:
        assert resolve_intent("Bash", "") == "execute"

    def test_custom_intent_maps(self) -> None:
        custom_sig = {"custom:cmd": "meta"}
        custom_bin = {"custom": "verify"}
        # Exact sig match takes priority
        assert resolve_intent("Bash", "custom:cmd", intent_sig_map=custom_sig) == "meta"
        # Binary fallback
        assert resolve_intent("Bash", "custom:other", intent_map=custom_bin) == "verify"

    def test_bash_binary_only_sig_no_colon(self) -> None:
        # A command_sig without colon, e.g. just "pytest"
        assert resolve_intent("Bash", "pytest") == "verify"


# ── _strip_wrapper_prefixes ─────────────────────────────────────────────


class TestStripWrapperPrefixes:
    """Tests for _strip_wrapper_prefixes."""

    def test_no_wrapper(self) -> None:
        tokens = ["pytest", "tests/"]
        assert _strip_wrapper_prefixes(tokens) == 0

    def test_sudo_stripped(self) -> None:
        tokens = ["sudo", "rm", "-rf", "/tmp/junk"]
        assert _strip_wrapper_prefixes(tokens) == 1

    def test_uv_run_stripped(self) -> None:
        tokens = ["uv", "run", "pytest", "tests/"]
        assert _strip_wrapper_prefixes(tokens) == 2

    def test_python_m_stripped(self) -> None:
        tokens = ["python", "-m", "pytest", "tests/"]
        assert _strip_wrapper_prefixes(tokens) == 2

    def test_python3_m_stripped(self) -> None:
        tokens = ["python3", "-m", "mypy", "."]
        assert _strip_wrapper_prefixes(tokens) == 2

    def test_env_strips_var_assignments(self) -> None:
        tokens = ["env", "FOO=bar", "BAZ=1", "python", "app.py"]
        idx = _strip_wrapper_prefixes(tokens)
        assert tokens[idx] == "python"

    def test_chained_wrappers(self) -> None:
        tokens = ["sudo", "env", "KEY=val", "python", "-m", "pytest"]
        idx = _strip_wrapper_prefixes(tokens)
        # sudo -> env -> skip KEY=val -> python -m -> pytest
        assert tokens[idx] == "pytest"

    def test_empty_tokens(self) -> None:
        assert _strip_wrapper_prefixes([]) == 0

    def test_nohup_wrapper(self) -> None:
        tokens = ["nohup", "node", "server.js"]
        assert _strip_wrapper_prefixes(tokens) == 1


# ── _extract_first_positional_arg ───────────────────────────────────────


class TestExtractFirstPositionalArg:
    """Tests for _extract_first_positional_arg."""

    def test_skips_flags(self) -> None:
        tokens = ["pytest", "-v", "--tb=short", "tests/test_foo.py"]
        result = _extract_first_positional_arg(tokens, 0)
        assert result == "tests/test_foo"

    def test_returns_default_when_no_positional(self) -> None:
        tokens = ["ls", "-la", "--color"]
        result = _extract_first_positional_arg(tokens, 0)
        assert result == "default"

    def test_strips_absolute_path_to_basename(self) -> None:
        tokens = ["python", "/usr/local/bin/script.py"]
        result = _extract_first_positional_arg(tokens, 0)
        assert result == "script"

    def test_redacts_secrets(self) -> None:
        # A long hex string should be considered a secret and skipped
        tokens = ["curl", "0123456789abcdef0123456789abcdef01234567"]
        result = _extract_first_positional_arg(tokens, 0)
        assert result == "default"

    def test_truncates_long_args(self) -> None:
        tokens = ["cmd", "a" * 100]
        result = _extract_first_positional_arg(tokens, 0)
        assert len(result) <= 30


# ── normalize_command_sig ───────────────────────────────────────────────


class TestNormalizeCommandSig:
    """Tests for normalize_command_sig."""

    def test_simple_command(self) -> None:
        assert normalize_command_sig("git status") == "git:status"

    def test_uv_run_prefix_stripped(self) -> None:
        result = normalize_command_sig("uv run python -m pytest tests/test_foo.py -v")
        assert result == "pytest:tests/test_foo"

    def test_empty_command_returns_unknown(self) -> None:
        assert normalize_command_sig("") == "unknown:unknown"
        assert normalize_command_sig("   ") == "unknown:unknown"

    def test_malformed_shell_command_fallback(self) -> None:
        # Unmatched quotes cause shlex.split to fail; fallback to str.split
        result = normalize_command_sig("echo 'unclosed")
        assert result.startswith("echo:")

    def test_absolute_path_binary_stripped(self) -> None:
        # /usr/bin/env tokenizes as a single token with path;
        # wrapper prefix check matches literal "env" not "/usr/bin/env",
        # so the binary becomes "env" (path stripped) with arg "python".
        result = normalize_command_sig("/usr/bin/env python script.py")
        assert result == "env:python"

    def test_only_wrappers_returns_unknown(self) -> None:
        result = normalize_command_sig("sudo env")
        assert result == "unknown:unknown"

    def test_binary_path_stripped_to_basename(self) -> None:
        result = normalize_command_sig("/home/user/.local/bin/ruff check .")
        assert result.startswith("ruff:")


# ── extract_error_sig ───────────────────────────────────────────────────


class TestExtractErrorSig:
    """Tests for extract_error_sig."""

    def test_empty_input_returns_empty(self) -> None:
        assert extract_error_sig("") == ""
        assert extract_error_sig("   ") == ""
        assert extract_error_sig(None) == ""  # type: ignore[arg-type]

    def test_single_line_error(self) -> None:
        result = extract_error_sig("ModuleNotFoundError: No module named 'foo'")
        assert "ModuleNotFoundError" in result

    def test_takes_last_meaningful_line(self) -> None:
        stderr = "Traceback (most recent call last):\n  File ...\nValueError: bad input\n"
        result = extract_error_sig(stderr)
        assert "ValueError" in result

    def test_skips_separator_lines(self) -> None:
        stderr = "Error occurred\n========\n"
        result = extract_error_sig(stderr)
        assert "Error occurred" in result

    def test_skips_exit_code_lines(self) -> None:
        stderr = "Something failed\nexit code: 1\n"
        result = extract_error_sig(stderr)
        assert "Something failed" in result

    def test_strips_absolute_paths(self) -> None:
        stderr = "Error in /home/user/project/src/module.py: syntax error"
        result = extract_error_sig(stderr)
        # Long absolute path should be reduced to basename
        assert "/home/user/project/src/" not in result
        assert "module" in result

    def test_strips_iso_timestamps(self) -> None:
        stderr = "2026-02-22T10:30:00.123 FATAL: crash"
        result = extract_error_sig(stderr)
        assert "2026-02-22" not in result
        assert "FATAL" in result

    def test_strips_bracketed_timestamps(self) -> None:
        stderr = "[12:34] Connection refused"
        result = extract_error_sig(stderr)
        assert "[12:34]" not in result
        assert "Connection refused" in result

    def test_truncates_long_output(self) -> None:
        stderr = "E" * 500
        result = extract_error_sig(stderr)
        assert len(result) <= 200


# ── error_memory_key ────────────────────────────────────────────────────


class TestErrorMemoryKey:
    """Tests for error_memory_key."""

    def test_empty_input_returns_empty(self) -> None:
        assert error_memory_key("") == ""
        assert error_memory_key("   ") == ""
        assert error_memory_key(None) == ""  # type: ignore[arg-type]

    def test_returns_hex_string(self) -> None:
        key = error_memory_key("ModuleNotFoundError: no module 'foo'")
        assert len(key) == 16
        assert all(c in "0123456789abcdef" for c in key)

    def test_deterministic(self) -> None:
        sig = "ValueError: bad input"
        assert error_memory_key(sig) == error_memory_key(sig)

    def test_whitespace_normalization(self) -> None:
        assert error_memory_key("foo  bar") == error_memory_key("foo bar")
        assert error_memory_key("  foo bar  ") == error_memory_key("foo bar")

    def test_case_insensitive(self) -> None:
        assert error_memory_key("ValueError") == error_memory_key("valueerror")

    def test_different_errors_produce_different_keys(self) -> None:
        k1 = error_memory_key("ValueError: bad")
        k2 = error_memory_key("TypeError: bad")
        assert k1 != k2


# ── Regex pattern sanity ────────────────────────────────────────────────


class TestRegexPatterns:
    """Verify compiled regex patterns match expected inputs."""

    def test_secret_pattern_matches_long_hex(self) -> None:
        assert _SECRET_PATTERN.search("a" * 10) is None
        assert _SECRET_PATTERN.search("0123456789abcdef" * 3) is not None

    def test_secret_pattern_matches_named_secret(self) -> None:
        assert _SECRET_PATTERN.search("token_abcdefgh1234") is not None
        assert _SECRET_PATTERN.search("sk_live_abcdefgh") is not None

    def test_abs_path_pattern_matches_deep_paths(self) -> None:
        m = _ABS_PATH_PATTERN.search("/home/user/project/file.py")
        assert m is not None
        assert m.group(1) == "file.py"

    def test_exit_code_line_matches_variants(self) -> None:
        assert _EXIT_CODE_LINE.match("exit code: 1")
        assert _EXIT_CODE_LINE.match("Exit_Code: 42")
        assert _EXIT_CODE_LINE.match("status: 0")
        assert _EXIT_CODE_LINE.match("exit 127")
        assert _EXIT_CODE_LINE.match("normal text") is None

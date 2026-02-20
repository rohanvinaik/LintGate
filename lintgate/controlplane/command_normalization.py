"""Command normalization and intent resolution for behavioral tracking.

Deterministic command signature extraction, error signature normalization,
and intent taxonomy mapping. No LLM calls, no file I/O.

Extracted from behavior_compass.py for module size compliance.
"""

from __future__ import annotations

import hashlib
import re
import shlex

# ── Intent taxonomy ─────────────────────────────────────────────────────
# 6 categories: enough structure to reduce ambiguity without NLP.

INTENT_CATEGORIES = frozenset({"inspect", "modify", "verify", "execute", "meta", "unknown"})

# Deterministic mapping chain:
# 1. Explicit tool type (Read, Write, Edit, Bash → fallback if not in sig map)
# 2. command_sig exact match (binary:subcommand)
# 3. Binary wildcard match (binary from binary:arg)
# 4. Fallback: Bash → "execute", other → "unknown"

_TOOL_TYPE_DEFAULTS: dict[str, str] = {
    "Read": "inspect",
    "Grep": "inspect",
    "Glob": "inspect",
    "Write": "modify",
    "Edit": "modify",
    "MultiEdit": "modify",
    "NotebookEdit": "modify",
    "WebFetch": "inspect",
    "WebSearch": "inspect",
    "Task": "meta",
    "TodoWrite": "meta",
    "AskUserQuestion": "meta",
}

# command_sig wildcard map for Bash: binary → intent
DEFAULT_INTENT_MAP: dict[str, str] = {
    # verify
    "pytest": "verify",
    "python": "execute",
    "ruff": "verify",
    "mypy": "verify",
    "flake8": "verify",
    "black": "modify",
    "isort": "modify",
    "cat": "inspect",
    "ls": "inspect",
    "head": "inspect",
    "tail": "inspect",
    "wc": "inspect",
    "file": "inspect",
    "stat": "inspect",
    "diff": "verify",
    "md5sum": "verify",
    "sha256sum": "verify",
    "xxd": "inspect",
    "hexdump": "inspect",
    "strings": "inspect",
    "find": "inspect",
    "du": "inspect",
    "readlink": "inspect",
    "which": "inspect",
    "type": "inspect",
    "command": "inspect",
    "test": "verify",
    "echo": "inspect",
    # modify
    "mkdir": "modify",
    "cp": "modify",
    "mv": "modify",
    "rm": "modify",
    "touch": "modify",
    "chmod": "modify",
    "chown": "modify",
    "sed": "modify",
    "awk": "modify",
    "tee": "modify",
    # execute
    "pip": "execute",
    "uv": "execute",
    "npm": "execute",
    "yarn": "execute",
    "make": "execute",
    "docker": "execute",
    "curl": "execute",
    "wget": "execute",
    # git default
    "git": "meta",
}

# More specific: "binary:subcommand" → intent (checked before binary-only)
DEFAULT_INTENT_SIG_MAP: dict[str, str] = {
    "git:status": "inspect",
    "git:log": "inspect",
    "git:diff": "verify",
    "git:show": "inspect",
    "git:branch": "inspect",
    "git:add": "modify",
    "git:commit": "modify",
    "git:push": "execute",
    "git:pull": "execute",
    "git:checkout": "modify",
    "git:merge": "execute",
    "git:rebase": "execute",
    "python:test": "verify",
    "python:pytest": "verify",
    # iOS tooling frequently uses hfsplus for inspection of extracted images.
    "hfsplus:ls": "inspect",
    "hfsplus:cat": "inspect",
    "hfsplus:rootfs": "inspect",
}


def resolve_intent(
    tool_name: str,
    command_sig: str,
    intent_map: dict[str, str] | None = None,
    intent_sig_map: dict[str, str] | None = None,
) -> str:
    """Resolve tool-use intent via deterministic mapping chain.

    Order: explicit tool type → command_sig exact match →
           binary wildcard match → fallback → unknown.
    """
    sig_map = intent_sig_map if intent_sig_map is not None else DEFAULT_INTENT_SIG_MAP
    bin_map = intent_map if intent_map is not None else DEFAULT_INTENT_MAP

    # 1. Non-Bash tools: explicit tool type lookup
    if tool_name != "Bash":
        return _TOOL_TYPE_DEFAULTS.get(tool_name, "unknown")

    # 2. Bash: try exact sig match first (e.g. "git:status")
    if command_sig and command_sig in sig_map:
        return sig_map[command_sig]

    # 3. Binary-only match (e.g. "pytest" from "pytest:tests")
    if command_sig:
        binary = command_sig.split(":")[0] if ":" in command_sig else command_sig
        if binary in bin_map:
            return bin_map[binary]

    # 4. Fallback: Bash default is "execute"
    return "execute"


# ── Command normalization ────────────────────────────────────────────────

_WRAPPER_PREFIXES = [
    ("uv", "run"),
    ("python", "-m"),
    ("python3", "-m"),
    ("env",),
    ("sudo",),
    ("nohup",),
    ("time",),
    ("nice",),
]

# Patterns that look like secrets — redact these
_SECRET_PATTERN = re.compile(
    r"(?:"
    r"[A-Za-z0-9+/]{40,}"  # Base64-ish long strings
    r"|[0-9a-f]{32,}"  # Long hex strings
    r"|(?:sk|pk|token|key|secret|password|auth)[_-]?\w{8,}"  # Named secrets
    r")",
    re.IGNORECASE,
)

# Patterns that look like absolute paths — strip to basename
_ABS_PATH_PATTERN = re.compile(r"/(?:[\w.-]+/){2,}([\w.-]+)")
_EXIT_CODE_LINE = re.compile(
    r"^(?:exit(?:[_ ]?(?:code|status))?|status)\s*[:=]?\s*\d+\s*$",
    re.IGNORECASE,
)


def _strip_wrapper_prefixes(tokens: list[str]) -> int:
    """Strip wrapper prefixes from token list, returning the index of the real binary."""
    i = 0
    while i < len(tokens):
        matched = False
        for prefix in _WRAPPER_PREFIXES:
            prefix_len = len(prefix)
            if i + prefix_len <= len(tokens) and all(
                tokens[i + j] == prefix[j] for j in range(prefix_len)
            ):
                i += prefix_len
                # For "env" wrapper, also skip VAR=val tokens
                if prefix == ("env",):
                    while i < len(tokens) and "=" in tokens[i]:
                        i += 1
                matched = True
                break
        if not matched:
            break
    return i


def _extract_first_positional_arg(tokens: list[str], start: int) -> str:
    """Extract and clean the first positional (non-flag, non-secret) argument."""
    for token in tokens[start + 1 :]:
        if token.startswith("-"):
            continue
        if _SECRET_PATTERN.search(token):
            continue
        # Strip absolute paths to basename-ish
        cleaned = _ABS_PATH_PATTERN.sub(r"\1", token)
        # Strip file extensions
        if "." in cleaned:
            cleaned = cleaned.rsplit(".", 1)[0]
        if cleaned:
            return cleaned[:30]
    return "default"


def normalize_command_sig(cmd: str) -> str:
    """Extract normalized command signature from a shell command.

    Strips wrapper prefixes, flags, paths, and secrets.
    Groups related commands under the same signature.

    Examples:
        "uv run python -m pytest tests/test_foo.py -v" → "pytest:tests"
        "idevicerestore -e custom.ipsw" → "idevicerestore:restore"
        "git status" → "git:status"
        "hfsplus rootfs.dec ls /Applications/" → "hfsplus:ls"
    """
    if not cmd or not cmd.strip():
        return "unknown:unknown"

    try:
        tokens = shlex.split(cmd)
    except ValueError:
        # Malformed shell command — split naively
        tokens = cmd.split()

    if not tokens:
        return "unknown:unknown"

    # Strip wrapper prefixes
    i = _strip_wrapper_prefixes(tokens)

    if i >= len(tokens):
        return "unknown:unknown"

    # Binary is the first non-wrapper token
    binary = tokens[i]
    # Strip path from binary if it's an absolute path
    if "/" in binary:
        binary = binary.rsplit("/", 1)[-1]

    first_arg = _extract_first_positional_arg(tokens, i)

    # Truncate to reasonable length
    binary = binary[:30]

    return f"{binary}:{first_arg}"


def extract_error_sig(stderr: str) -> str:
    """Extract normalized error signature from stderr output.

    Takes the last non-empty line, strips absolute paths and timestamps.
    Used as constraint key for deduplication.
    """
    if not stderr or not stderr.strip():
        return ""

    lines = stderr.strip().splitlines()

    # Walk backwards to find a meaningful line
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # Skip lines that are just separator characters
        if all(c in "-=_*#" for c in stripped):
            continue
        # Skip status-only lines (not useful failure signatures)
        if _EXIT_CODE_LINE.match(stripped):
            continue

        # Strip absolute paths
        cleaned = _ABS_PATH_PATTERN.sub(r"\1", stripped)
        # Strip timestamps (common patterns: ISO, syslog, bracketed)
        cleaned = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\d]*\s*", "", cleaned)
        cleaned = re.sub(r"\[\d+[:.]\d+\]\s*", "", cleaned)

        # Truncate to reasonable length
        return cleaned[:200]

    return ""


def error_memory_key(error_sig: str) -> str:
    """Build a stable hash key for error-memory aggregation."""
    normalized = re.sub(r"\s+", " ", (error_sig or "").strip().lower())
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]

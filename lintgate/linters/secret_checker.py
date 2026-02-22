"""High-signal secret scanner for changed files.

Built-in (no external dependencies) security linter focused on obvious
credential leaks. This complements bandit/pip-audit with deterministic
pattern checks that run in normal lint tiers.
"""

from __future__ import annotations

from contextlib import suppress
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from ..types import LinterContext, LintIssue
from .base import BaseLinter

if TYPE_CHECKING:
    from collections.abc import Iterable


_MAX_FILE_BYTES = 1_000_000  # Skip unusually large files to keep hook latency stable.
_LIKELY_PLACEHOLDER_RE = re.compile(
    r"(example|sample|dummy|placeholder|changeme|replace_me|xxxxx|test(_|-)?key)",
    re.IGNORECASE,
)

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str], float, str]] = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}"), 0.98, "warning"),
    (
        "private_key",
        re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
        1.0,
        "blocking",
    ),
    ("github_token", re.compile(r"(ghp_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9_]{20,}"), 0.98, "warning"),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"), 0.95, "warning"),
    ("stripe_secret", re.compile(r"sk_live_[A-Za-z0-9]{16,}"), 0.98, "warning"),
    ("generic_secret_assignment", re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"][^'\"]{12,}['\"]"), 0.80, "informational"),
]


class SecretChecker(BaseLinter):
    """Detect obvious hardcoded credentials and key material."""

    name = "secret_checker"
    tier = 2
    timeout_ms = 3000
    required_tool = None

    def run(self, ctx: LinterContext) -> Iterable[LintIssue]:
        for path in self._filter_files(ctx.files):
            if not os.path.exists(path):
                continue

            with suppress(OSError):
                if os.path.getsize(path) > _MAX_FILE_BYTES:
                    continue

            with suppress(OSError):
                text = Path(path).read_text(encoding="utf-8", errors="ignore")
                for line_no, line in enumerate(text.splitlines(), start=1):
                    if _LIKELY_PLACEHOLDER_RE.search(line):
                        continue
                    has_specific_match = False
                    for name, pattern, confidence, severity in _SECRET_PATTERNS:
                        if name == "generic_secret_assignment" and has_specific_match:
                            continue
                        match = pattern.search(line)
                        if not match:
                            continue
                        if name != "generic_secret_assignment":
                            has_specific_match = True
                        snippet = match.group(0)
                        snippet_preview = _redact(snippet)
                        yield LintIssue(
                            linter=self.name,
                            kind=name,
                            message=f"Potential secret detected ({name}): {snippet_preview}",
                            file=path,
                            line=line_no,
                            severity=severity,
                            confidence=confidence,
                            evidence={
                                "pattern": name,
                                "match_preview": snippet_preview,
                            },
                            suggestions=[
                                "Move secret to environment variable or secret manager",
                                "Rotate credential if this value is real",
                                "Add file/pattern to ignore list only if this is intentionally synthetic",
                            ],
                        )

    def _filter_files(self, files: list[str]) -> list[str]:
        allowed_suffixes = {
            ".py",
            ".env",
            ".txt",
            ".md",
            ".yaml",
            ".yml",
            ".json",
            ".ini",
            ".cfg",
            ".toml",
            ".sh",
        }
        filtered: list[str] = []
        for file_path in files:
            p = Path(file_path)
            if p.suffix.lower() in allowed_suffixes or p.name.startswith(".env"):
                filtered.append(file_path)
        return filtered


def _redact(value: str) -> str:
    if len(value) <= 10:
        return value[:2] + "***"
    return value[:4] + "***" + value[-4:]

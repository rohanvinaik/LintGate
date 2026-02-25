#!/usr/bin/env python3
"""Validate required-check dependency profile contract.

This script ensures that the required-check profile has all dependencies
necessary to run the CI test suite. It validates explicit import contracts
to prevent the split-brain invariant issue identified in #96.

Usage:
    python scripts/check_required_profile.py --profile required-checks

Exit codes:
    0 - All required modules are available
    1 - One or more required modules are missing
"""

from __future__ import annotations

import argparse
import sys

# Explicit required import set for the required-check profile.
# This list encodes the minimum dependencies needed to run the test suite.
# Modules are grouped by their optional-dependencies entry in pyproject.toml.
REQUIRED_IMPORTS = {
    # Core test framework (always required)
    "pytest": ["pytest"],
    # Algebra optional deps - previously drifted (see #96)
    "algebra": ["hypothesis", "icontract"],
    # Core runtime modules exercised by required checks
    "runtime": [
        "lintgate",
        "lintgate.config",
        "lintgate.state",
        "lintgate.types",
        "lintgate.mutation.state",
        "lintgate.mutation.engine",
        "lintgate.mutation.policy",
        "lintgate.linters.performance_checks.manifest",
        "lintgate.linters.performance_checks.algebra_types",
        "lintgate.channels.mutation_channel",
        "mcp_tools.mutation_tools",
    ],
}


def check_import(module_name: str) -> tuple[bool, str]:
    """Attempt to import a module and return (success, error_message)."""
    try:
        __import__(module_name)
        return True, ""
    except ImportError as e:
        return False, str(e)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def validate_profile(profile: str) -> tuple[bool, dict[str, list[str]]]:
    """Validate the required profile has all necessary imports.

    Returns:
        (is_valid, missing_modules) where missing_modules maps category to list of missing modules.
    """
    if profile != "required-checks":
        print(f"Unknown profile: {profile}")
        return False, {}

    missing: dict[str, list[str]] = {}

    for category, modules in REQUIRED_IMPORTS.items():
        missing_in_category = []
        for module in modules:
            success, error = check_import(module)
            if not success:
                missing_in_category.append(f"{module} ({error})")

        if missing_in_category:
            missing[category] = missing_in_category

    return len(missing) == 0, missing


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Validate required-check dependency profile contract"
    )
    parser.add_argument(
        "--profile",
        default="required-checks",
        help="Profile to validate (default: required-checks)",
    )
    args = parser.parse_args()

    print(f"Validating profile: {args.profile}")
    print("-" * 50)

    is_valid, missing = validate_profile(args.profile)

    if is_valid:
        print("PASS: All required modules are available")
        return 0

    print("FAIL: Missing required modules:")
    for category, modules in missing.items():
        print(f"\n  [{category}]")
        for module in modules:
            print(f"    - {module}")

    print("\n" + "-" * 50)
    print("To fix, install required dependencies:")
    print("  pip install -e '.[dev]'")
    print("\nOr for minimal required profile:")
    print("  pip install pytest hypothesis icontract")
    print("  pip install -e '.'")

    return 1


if __name__ == "__main__":
    sys.exit(main())

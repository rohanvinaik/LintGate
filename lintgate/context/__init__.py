"""Context subpackage — context health auditing, bootstrap, and guidance.

Canonical home for the context_* modules. Backward-compatible shims in
the parent ``lintgate/`` package re-export from here so that existing
imports (``from lintgate.context_auditor import ...``) continue to work.

Imports are lazy to avoid circular dependency chains through
``theory_extractor`` (which imports ``context_guidance``).
"""

"""Manifest-driven wiki generation for LintGate.

Renders navigable wiki pages from source markdown documents using a
declarative YAML manifest. V1 is local-only — pages materialize to
``.lintgate/wiki/``. GitHub wiki push deferred to V2.

Modules:
- manifest: Schema + YAML loader
- extractor: Section extraction (reuses theory_extractor parsing patterns)
- composer: Page composition + inferred cross-links
- freshness: Hash-based staleness tracking
"""

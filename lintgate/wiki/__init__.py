"""Manifest-driven wiki generation and publishing for LintGate.

Renders navigable wiki pages from source markdown documents using a
declarative YAML manifest (``wiki.yaml`` or ``.lintgate/wiki_manifest.yaml``).
Supports dual publishing to GitHub Wiki tab and GitHub Pages static site.

Modules:
- manifest: Schema + YAML loader (rails, chapters, prerequisites, metrics)
- extractor: Section extraction (reuses theory_extractor parsing patterns)
- composer: Page composition + inferred cross-links
- transforms: Shared transform pipeline (both publishers)
- freshness: Hash-based staleness tracking
- pages_publisher: Static site generator (GitHub Pages)
- link_checker: Link integrity + config completeness validation
"""

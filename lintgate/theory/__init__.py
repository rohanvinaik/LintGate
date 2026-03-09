"""Theory subpackage — groups theory_discovery, theory_extractor, and theory_scoring.

Re-exports all public names from the moved modules for backward compatibility.
Any import like ``from lintgate.theory.discovery import X`` or
``from lintgate.theory.scoring import X`` will resolve here.
"""

from .discovery import (  # noqa: F401
    _EXTRA_MD_SKIP_DIRS,
    _MAX_MD_FILES,
    _discover_md_files,
    _has_frontmatter_opt_out,
    _parse_document,
    _scan_priority_dir,
    _Section,
    extract_docstring_claims,
)
from .scoring import (  # noqa: F401
    _CONTRASTIVE_MARKERS,
    _FACET_SCORERS,
    _THEORY_HEADING_SIGNALS,
    _THEORY_PARAGRAPH_SIGNALS,
    _classify_section,
    _pick_best_summary_claim,
    _score_claim,
    _split_sentences,
)

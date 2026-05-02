"""Pure predicates over a Synthesis; the eval runner owns regression semantics.

In ``--live`` mode the Corpus Protocol exposes neither payload sources nor
bodies, so the runner skips ``all_sources_in_allowed_domains`` /
``claims_grounded_in_body`` (they'd vacuously pass) and builds the allowlist
itself.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast
from urllib.parse import urlparse

if TYPE_CHECKING:
    from slopmortem.models import Synthesis


def where_diverged_nonempty(s: Synthesis) -> bool:
    return bool(s.where_diverged and s.where_diverged.strip())


def all_sources_in_allowed_domains(s: Synthesis, allowed_hosts: set[str]) -> bool:
    """Empty ``s.sources`` is vacuously True; an unresolvable hostname counts as a miss."""
    for url in s.sources:
        host = urlparse(url).hostname
        if host is None:
            return False
        if host not in allowed_hosts:
            return False
    return True


def lifespan_months_positive(s: Synthesis) -> bool:
    if s.lifespan_months is None:
        return True
    return s.lifespan_months > 0


# Trailing-word capture catches fabricated qualifiers: "1.7 million US customers"
# matches as "1.7 million US", which then fails the substring check against
# body "1.7 million customers".
_NUMERIC_CLAIM_RE = re.compile(
    r"""
    \$?(?:\d[\d,.]*\d|\d)                          # currency-prefixed digit cluster
    (?:\s*(?:million|billion|[MBK]|%|months?|years?))?  # optional unit qualifier
    (?:\s+\w+)?                                    # optional one trailing word
    """,
    re.VERBOSE,
)


def claims_grounded_in_body(s: Synthesis, body: str) -> bool:
    """Every numeric-looking claim in ``s`` appears verbatim in ``body``.

    Tolerant of false positives; re-record the baseline if the rule
    legitimately disagrees.
    """
    rationales = (
        s.why_similar,
        s.similarity.business_model.rationale,
        s.similarity.market.rationale,
        s.similarity.gtm.rationale,
        s.similarity.stage_scale.rationale,
    )
    for prose in rationales:
        if not prose:
            continue
        matches = cast("list[str]", _NUMERIC_CLAIM_RE.findall(prose))
        for match in matches:
            if match not in body:
                return False
    return True

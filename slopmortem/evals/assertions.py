"""Pure assertions over a Synthesis, used by the eval runner.

Regression semantics live in the runner module docstring; these functions
are just the predicates.

These functions are pure: no I/O, no async, no module-level state. The
runner constructs the allowed_hosts set itself, because the read-side
Corpus Protocol does not expose payload.sources.

claims_grounded_in_body requires the candidate body. In --live mode the
public Corpus Protocol does not expose bodies, so the runner emits True
vacuously (mirroring how all_sources_in_allowed_domains collapses to the
fixed allowlist there).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast
from urllib.parse import urlparse

if TYPE_CHECKING:
    from slopmortem.models import Synthesis


def where_diverged_nonempty(s: Synthesis) -> bool:
    """True iff where_diverged has at least one non-whitespace char."""
    return bool(s.where_diverged and s.where_diverged.strip())


def all_sources_in_allowed_domains(s: Synthesis, allowed_hosts: set[str]) -> bool:
    """True iff every URL in s.sources resolves to a host in allowed_hosts.

    Empty s.sources is vacuously True. URLs that urlparse cannot resolve
    to a hostname count as a miss.
    """
    for url in s.sources:
        host = urlparse(url).hostname
        if host is None:
            return False
        if host not in allowed_hosts:
            return False
    return True


def lifespan_months_positive(s: Synthesis) -> bool:
    """True iff lifespan_months is None or strictly positive."""
    if s.lifespan_months is None:
        return True
    return s.lifespan_months > 0


# Trailing-word capture catches fabricated qualifiers: "1.7 million US customers"
# matches as "1.7 million US", which then fails the substring check against body
# "1.7 million customers". re.VERBOSE allows the inline `#` annotations.
_NUMERIC_CLAIM_RE = re.compile(
    r"""
    \$?(?:\d[\d,.]*\d|\d)                          # currency-prefixed digit cluster
    (?:\s*(?:million|billion|[MBK]|%|months?|years?))?  # optional unit qualifier
    (?:\s+\w+)?                                    # optional one trailing word
    """,
    re.VERBOSE,
)


def claims_grounded_in_body(s: Synthesis, body: str) -> bool:
    """True iff every numeric-looking claim in s appears verbatim in body.

    Scans why_similar and the four similarity.*.rationale strings. False
    positives are tolerated; re-record the baseline when the rule
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

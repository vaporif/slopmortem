"""Pure assertions over a :class:`Synthesis`, used by the eval runner.

Regression semantics (also documented on the runner):

- An assertion that returned ``True`` in the recorded baseline but ``False``
  in the current run is a **regression**. The runner exits non-zero in that
  case.
- An assertion missing from the baseline is treated as forward-compat — the
  runner emits a warning but does not fail.
- An assertion that flipped from ``False`` -> ``True`` is an improvement,
  not a regression.

These functions are pure: no I/O, no async, no module-level state. The
runner constructs the ``allowed_hosts`` set itself, because the read-side
:class:`slopmortem.corpus.store.Corpus` Protocol does not expose the
candidate ``payload.sources`` needed for a per-candidate domain check.

``claims_grounded_in_body`` requires the candidate ``body`` text. The runner
fetches that via the private ``_EvalCorpus.lookup_payload`` in deterministic
mode. In ``--live`` mode the public Corpus Protocol does not expose payload
bodies, so the runner emits ``True`` vacuously (mirroring how
``all_sources_in_allowed_domains`` collapses to the fixed allowlist there).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast
from urllib.parse import urlparse

if TYPE_CHECKING:
    from slopmortem.models import Synthesis


def where_diverged_nonempty(s: Synthesis) -> bool:
    """Return True iff ``s.where_diverged`` has at least one non-whitespace char."""
    return bool(s.where_diverged and s.where_diverged.strip())


def all_sources_in_allowed_domains(s: Synthesis, allowed_hosts: set[str]) -> bool:
    """Return True iff every URL in ``s.sources`` resolves to a host in *allowed_hosts*.

    Empty ``s.sources`` is vacuously True. URLs that ``urlparse`` cannot
    resolve to a hostname (``hostname is None``) count as a miss.
    """
    for url in s.sources:
        host = urlparse(url).hostname
        if host is None:
            return False
        if host not in allowed_hosts:
            return False
    return True


def lifespan_months_positive(s: Synthesis) -> bool:
    """Return True iff ``s.lifespan_months`` is unknown (None) or strictly positive."""
    if s.lifespan_months is None:
        return True
    return s.lifespan_months > 0


# Match a numeric token plus optional unit qualifier plus optional one trailing
# word. The trailing word is what catches fabricated qualifiers like
# "1.7 million US customers" -> regex extracts "1.7 million US"; substring check
# against body "1.7 million customers" then fails.
#
# Digit cluster: ``\d[\d,.]*\d`` requires both ends to be digits, so a sentence-
# terminating ``.`` is excluded (single-digit ``\d`` covers the standalone case).
# Currency prefix ``$`` is optional. Qualifier is one of common units; matched
# case-insensitively so "Million"/"million" both extract, but the substring
# check stays case-sensitive (intentional strictness).
_NUMERIC_CLAIM_RE = re.compile(
    r"""
    \$?(?:\d[\d,.]*\d|\d)                          # currency-prefixed digit cluster
    (?:\s*(?:million|billion|[MBK]|%|months?|years?))?  # optional unit qualifier
    (?:\s+\w+)?                                    # optional one trailing word
    """,
    re.IGNORECASE | re.VERBOSE,
)


def claims_grounded_in_body(s: Synthesis, body: str) -> bool:
    """Return True iff every numeric-looking claim in *s* appears verbatim in *body*.

    Scans ``s.why_similar`` and the four ``s.similarity.*.rationale`` strings.
    Verbatim is intentional — we want to catch "1.7 million US customers" when
    the body only says "1.7 million customers".

    Cheap regression gate. Scans these prose strings on *s*:

    - ``s.why_similar``
    - ``s.similarity.business_model.rationale``
    - ``s.similarity.market.rationale``
    - ``s.similarity.gtm.rationale``
    - ``s.similarity.stage_scale.rationale``

    Each numeric token (with optional currency prefix, optional unit, optional
    one trailing word) must appear as a substring in *body*. Empty prose is
    vacuously True. A non-empty body is required when any claim contains a
    digit; an empty body with any numeric claim returns False.

    False positives are tolerated by design — the runner has ``--write-baseline``
    to re-record when the rule legitimately disagrees.
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
        # ``re.Pattern.findall`` returns ``list[Any]`` because the element type
        # depends on the pattern's groups. Our pattern has no capturing groups,
        # so every element is a ``str`` (the whole match).
        matches = cast("list[str]", _NUMERIC_CLAIM_RE.findall(prose))
        for match in matches:
            if match not in body:
                return False
    return True

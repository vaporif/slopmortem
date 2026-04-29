"""Three pure assertions over a :class:`Synthesis`, used by the eval runner.

Regression semantics (also documented on the runner):

- An assertion that returned ``True`` in the recorded baseline but ``False``
  in the current run is a **regression**. The runner exits non-zero in that
  case.
- An assertion missing from the baseline is treated as forward-compat — the
  runner emits a warning but does not fail.
- An assertion that flipped from ``False`` -> ``True`` is an improvement,
  not a regression.

These functions are pure: no I/O, no async, no module-level state. The
runner constructs the ``allowed_hosts`` set itself (because the read-side
:class:`slopmortem.corpus.store.Corpus` Protocol does not expose the
candidate ``payload.sources`` needed for a per-candidate domain check).
"""

from typing import TYPE_CHECKING
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

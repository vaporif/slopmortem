"""Ingest pipeline: gather → slop classify → fan-out → embed → upsert."""

from __future__ import annotations

from slopmortem.ingest._orchestrator import (
    INGEST_PHASE_LABELS as INGEST_PHASE_LABELS,
)
from slopmortem.ingest._orchestrator import (
    Corpus as Corpus,
)
from slopmortem.ingest._orchestrator import (
    FakeSlopClassifier as FakeSlopClassifier,
)
from slopmortem.ingest._orchestrator import (
    HaikuSlopClassifier as HaikuSlopClassifier,
)
from slopmortem.ingest._orchestrator import (
    IngestPhase as IngestPhase,
)
from slopmortem.ingest._orchestrator import (
    IngestResult as IngestResult,
)
from slopmortem.ingest._orchestrator import (
    InMemoryCorpus as InMemoryCorpus,
)
from slopmortem.ingest._orchestrator import (
    SlopClassifier as SlopClassifier,
)
from slopmortem.ingest._orchestrator import (
    _Point as _Point,
)
from slopmortem.ingest._orchestrator import (
    ingest as ingest,
)

__all__ = [
    "INGEST_PHASE_LABELS",
    "Corpus",
    "FakeSlopClassifier",
    "HaikuSlopClassifier",
    "InMemoryCorpus",
    "IngestPhase",
    "IngestResult",
    "SlopClassifier",
    "_Point",
    "ingest",
]

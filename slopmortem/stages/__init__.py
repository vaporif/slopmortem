"""Pipeline stages: facet_extract, retrieve, llm_rerank, synthesize, consolidate_risks."""

from __future__ import annotations

from slopmortem.stages.consolidate_risks import consolidate_risks as consolidate_risks
from slopmortem.stages.facet_extract import extract_facets as extract_facets
from slopmortem.stages.llm_rerank import llm_rerank as llm_rerank
from slopmortem.stages.retrieve import (
    SparseEncoder as SparseEncoder,
    retrieve as retrieve,
)
from slopmortem.stages.synthesize import (
    synthesize as synthesize,
    synthesize_all as synthesize_all,
    synthesize_prompt_kwargs as synthesize_prompt_kwargs,
)

__all__ = [
    "SparseEncoder",
    "consolidate_risks",
    "extract_facets",
    "llm_rerank",
    "retrieve",
    "synthesize",
    "synthesize_all",
    "synthesize_prompt_kwargs",
]

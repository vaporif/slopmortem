"""LLM and embedding clients, prompt rendering, and OpenRouter retry logic."""

from __future__ import annotations

from slopmortem.llm.cassettes import (
    NoCannedEmbeddingError as NoCannedEmbeddingError,
    embed_cassette_key as embed_cassette_key,
    llm_cassette_key as llm_cassette_key,
    template_sha as template_sha,
)
from slopmortem.llm.client import (
    CompletionResult as CompletionResult,
    LLMClient as LLMClient,
)
from slopmortem.llm.embedding_client import (
    EmbeddingClient as EmbeddingClient,
    EmbeddingResult as EmbeddingResult,
)
from slopmortem.llm.embedding_factory import make_embedder as make_embedder
from slopmortem.llm.fake import (
    FakeLLMClient as FakeLLMClient,
    FakeResponse as FakeResponse,
    NoCannedResponseError as NoCannedResponseError,
)
from slopmortem.llm.fake_embeddings import FakeEmbeddingClient as FakeEmbeddingClient
from slopmortem.llm.fastembed_client import FastEmbedEmbeddingClient as FastEmbedEmbeddingClient
from slopmortem.llm.openai_embeddings import (
    EMBED_DIMS as EMBED_DIMS,
    OpenAIEmbeddingClient as OpenAIEmbeddingClient,
)
from slopmortem.llm.openrouter import (
    OpenRouterClient as OpenRouterClient,
    gather_with_limit as gather_with_limit,
    is_transient_http as is_transient_http,
)
from slopmortem.llm.prompts import (
    prompt_template_sha as prompt_template_sha,
    render_blocks as render_blocks,
    render_prompt as render_prompt,
)
from slopmortem.llm.tools import (
    synthesis_tools as synthesis_tools,
    to_openai_input_schema as to_openai_input_schema,
    to_strict_response_schema as to_strict_response_schema,
)

__all__ = [
    "EMBED_DIMS",
    "CompletionResult",
    "EmbeddingClient",
    "EmbeddingResult",
    "FakeEmbeddingClient",
    "FakeLLMClient",
    "FakeResponse",
    "FastEmbedEmbeddingClient",
    "LLMClient",
    "NoCannedEmbeddingError",
    "NoCannedResponseError",
    "OpenAIEmbeddingClient",
    "OpenRouterClient",
    "embed_cassette_key",
    "gather_with_limit",
    "is_transient_http",
    "llm_cassette_key",
    "make_embedder",
    "prompt_template_sha",
    "render_blocks",
    "render_prompt",
    "synthesis_tools",
    "template_sha",
    "to_openai_input_schema",
    "to_strict_response_schema",
]

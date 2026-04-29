"""Synthesize stage: per-candidate post-mortem generation with cache-warm fan-out.

One candidate per ``synthesize`` call. ``synthesize_all`` runs the cache-warm
pattern (first call alone, then :func:`gather_resilient` on the rest) so one
candidate's failure does not cancel the others.

The OpenRouter client (``slopmortem/llm/openrouter.py``) drives the tool-call
loop, wraps tool results in ``<untrusted_document>`` tags, and enforces the
5-turn bound; this stage is one ``llm.complete(...)`` call. ``Laminar.init``
wiring lands in Task 10 (per plan line 713); the module-level ``_emit_event``
hook is a no-op stub until that orchestration ships.
"""

from typing import TYPE_CHECKING
from urllib.parse import urlparse

from lmnr import Laminar

from slopmortem.concurrency import gather_resilient
from slopmortem.llm.prompts import prompt_template_sha, render_prompt
from slopmortem.llm.tools import synthesis_tools, to_strict_response_schema
from slopmortem.models import Synthesis
from slopmortem.tracing.events import SpanEvent

if TYPE_CHECKING:
    from slopmortem.config import Config
    from slopmortem.llm.client import LLMClient
    from slopmortem.models import Candidate, InputContext

# Fixed host allowlist applied on top of the per-candidate
# ``payload.sources`` hosts. ``web.archive.org`` is intentionally NOT
# included — Wayback proxies arbitrary URLs and bypasses host-level
# allowlist semantics; see spec §995-1006.
_FIXED_HOST_ALLOWLIST: frozenset[str] = frozenset({"news.ycombinator.com"})

# Literal contract written into ``synthesize.j2``: the LLM must put this
# exact string in ``where_diverged`` when it detects an injection attempt.
_INJECTION_MARKER = "prompt_injection_attempted"


def _emit_event(event: SpanEvent) -> None:
    """Emit *event* as a Laminar span event when tracing is initialized.

    Tests monkeypatch this to observe emissions; in production the body fires
    ``Laminar.event(name=str(event))`` when ``Laminar.is_initialized()`` is
    true. ``Laminar.event`` already guards on initialization itself, but we
    check here to keep the seam explicit and avoid surprise traces from
    misconfigured fixtures.
    """
    if Laminar.is_initialized():
        Laminar.event(name=str(event))


async def synthesize(
    candidate: Candidate,
    ctx: InputContext,
    llm: LLMClient,
    config: Config,
    *,
    model: str | None = None,
) -> Synthesis:
    """Generate a single :class:`Synthesis` for *candidate* against the user pitch in *ctx*.

    Args:
        candidate: One :class:`Candidate` from the rerank top-N. Its
            ``payload.body`` is inlined into the prompt inside
            ``<untrusted_document>`` tags.
        ctx: The user's :class:`InputContext`; ``ctx.description`` is the
            pitch.
        llm: Async :class:`LLMClient`; ``cache=True`` is set so the system
            block hits the prompt cache across calls within the 5-min TTL.
        config: :class:`Config`. Drives ``synthesis_tools`` (Tavily inclusion)
            and is reserved for future per-stage knobs.
        model: Optional model override; ``None`` lets the client pick.

    Returns:
        The parsed :class:`Synthesis`. ``sources`` is filtered against
        ``candidate.payload.sources`` hosts plus ``news.ycombinator.com``;
        off-allowlist URLs are dropped silently (no per-URL span event in
        the closed enum). When the LLM marks ``where_diverged ==
        "prompt_injection_attempted"``, ``_emit_event`` fires
        :data:`SpanEvent.PROMPT_INJECTION_ATTEMPTED`.

    Raises:
        ValidationError: The LLM emitted JSON that doesn't validate against
            :class:`Synthesis`.
    """
    prompt = render_prompt(
        "synthesize",
        pitch=ctx.description,
        candidate_id=candidate.canonical_id,
        candidate_name=candidate.payload.name,
        candidate_body=candidate.payload.body,
    )
    result = await llm.complete(
        prompt,
        model=model,
        cache=True,
        tools=synthesis_tools(config),
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "Synthesis",
                "schema": to_strict_response_schema(Synthesis),
                "strict": True,
            },
        },
        extra_body={
            "provider": {"require_parameters": True},
            "prompt_template_sha": prompt_template_sha("synthesize"),
        },
    )
    parsed = Synthesis.model_validate_json(result.text)

    if parsed.where_diverged.strip() == _INJECTION_MARKER:
        _emit_event(SpanEvent.PROMPT_INJECTION_ATTEMPTED)

    allowed_hosts = _build_allowed_hosts(candidate.payload.sources)
    filtered_sources = [url for url in parsed.sources if _hostname(url) in allowed_hosts]
    return parsed.model_copy(update={"sources": filtered_sources})


def _hostname(url: str) -> str | None:
    """Best-effort hostname extraction; never raises."""
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


def _build_allowed_hosts(candidate_sources: list[str]) -> frozenset[str]:
    """Union of candidate-source hosts and the fixed allowlist (``news.ycombinator.com``)."""
    candidate_hosts = {h for src in candidate_sources if (h := _hostname(src))}
    return frozenset(candidate_hosts | _FIXED_HOST_ALLOWLIST)


async def synthesize_all(
    candidates: list[Candidate],
    ctx: InputContext,
    llm: LLMClient,
    config: Config,
    *,
    model: str | None = None,
) -> list[Synthesis | BaseException]:
    """Cache-warm synthesize fan-out: one warm call, then :func:`gather_resilient`.

    The first call runs alone so the prompt cache is populated before the
    parallel fan-out hits it (avoiding a pile-up of cache-write races). The
    remaining calls run via :func:`gather_resilient` so a single failed
    candidate does not cancel its siblings; the reporting path filters
    exceptions out and notes the gap on ``Report.candidates``.

    Args:
        candidates: All candidates to synthesize. May be empty.
        ctx: The user's :class:`InputContext`.
        llm: Async :class:`LLMClient`.
        config: :class:`Config`.
        model: Optional model override.

    Returns:
        A list the same length as *candidates*, each entry either a
        :class:`Synthesis` or the :class:`BaseException` raised on its
        behalf.
    """
    if not candidates:
        return []

    first: Synthesis | BaseException
    try:
        first = await synthesize(candidates[0], ctx, llm, config, model=model)
    except Exception as exc:  # noqa: BLE001 — record the failure as a list entry, not a raise
        first = exc

    rest_results = await gather_resilient(
        *(synthesize(c, ctx, llm, config, model=model) for c in candidates[1:]),
    )
    return [first, *rest_results]

"""Runtime implementations of the ingest ports.

`InMemoryCorpus` is for tests. `FakeSlopClassifier` is for tests and dry-run.
`HaikuSlopClassifier` is the production slop classifier.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from slopmortem.ingest._ports import _Point
from slopmortem.llm import prompt_template_sha, render_prompt

if TYPE_CHECKING:
    from slopmortem.llm import LLMClient

__all__ = [
    "FakeSlopClassifier",
    "HaikuSlopClassifier",
    "InMemoryCorpus",
]


@dataclass
class InMemoryCorpus:
    """In-memory `Corpus` for tests; not used in production."""

    points: list[_Point] = field(default_factory=list)

    async def upsert_chunk(self, point: object) -> None:
        if not isinstance(point, _Point):
            msg = f"InMemoryCorpus expects _Point, got {type(point).__name__}"
            raise TypeError(msg)
        self.points.append(point)

    async def has_chunks(self, canonical_id: str) -> bool:
        return any(p.payload.get("canonical_id") == canonical_id for p in self.points)

    async def delete_chunks_for_canonical(self, canonical_id: str) -> None:
        self.points = [p for p in self.points if p.payload.get("canonical_id") != canonical_id]


@dataclass
class FakeSlopClassifier:
    """Deterministic test `SlopClassifier`; ``scores`` overrides by text-key prefix."""

    default_score: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)

    async def score(self, text: str) -> float:
        for key, val in self.scores.items():
            if text.startswith(key) or key in text:
                return val
        return self.default_score


@dataclass
class HaikuSlopClassifier:
    """LLM-backed slop classifier.

    Asks Haiku whether a text describes a dead company; returns 0.0 if yes,
    else 1.0 (above the default ``slop_threshold=0.7``, so quarantines).

    ``char_limit=6000`` so the demise narrative falls inside the window for long
    obituaries (Sun, WeWork). Tighter 1500-char caps caused false-negative
    quarantines.
    """

    llm: LLMClient
    model: str
    char_limit: int = 6000
    max_tokens: int | None = None

    async def score(self, text: str) -> float:
        snippet = text[: self.char_limit]
        prompt = render_prompt("slop_judge", text=snippet)
        result = await self.llm.complete(
            prompt,
            model=self.model,
            cache=False,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "SlopJudge",
                    "schema": {
                        "type": "object",
                        "properties": {"is_dead_company": {"type": "boolean"}},
                        "required": ["is_dead_company"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            },
            extra_body={"prompt_template_sha": prompt_template_sha("slop_judge")},
            max_tokens=self.max_tokens,
        )
        try:
            # json.loads is typed Any; narrow to object so downstream isinstance checks gate it.
            parsed = cast("object", json.loads(result.text))
        except json.JSONDecodeError:
            # Conservative on parse failure: keep the entry rather than silently drop.
            return 0.0
        if not isinstance(parsed, dict):
            return 1.0
        is_dead = cast("dict[str, object]", parsed).get("is_dead_company")
        return 0.0 if is_dead is True else 1.0

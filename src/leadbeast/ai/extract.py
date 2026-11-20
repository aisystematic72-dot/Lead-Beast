"""Cited extraction: every value must carry a span that appears verbatim in the source text."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ValidationError

from leadbeast.core.ports import ModelClient

Field_ = Literal["years_in_business", "team_size", "booking_method", "services"]

PROMPT_VERSION = "extract-v1"
PROMPT = """You extract facts about a local business from its website text.
Return a JSON array. Each item: {{"field": one of {fields}, "value": string, "span": string}}.
"span" MUST be copied exactly, character for character, from the text below.
Only include a field if the text states it. Do not guess.

TEXT:
\"\"\"{text}\"\"\"
"""


class Extraction(BaseModel):
    field: Field_
    value: str
    span: str


def build_prompt(text: str, fields: list[str]) -> str:
    return PROMPT.format(fields=fields, text=text[:8000])


def cited_only(raw: list[Extraction], text: str) -> list[Extraction]:
    """Drop any claim whose span is not a verbatim substring of the fetched text."""
    return [e for e in raw if e.span and e.span.strip() and e.span in text]


async def extract(model: ModelClient, text: str, fields: list[str]) -> list[Extraction] | None:
    """Returns cited extractions, or None when the model output was invalid twice."""
    for _ in range(2):  # invalid output is retried once
        try:
            raw = await model.json(prompt=build_prompt(text, fields), schema=list[Extraction])
            assert isinstance(raw, list)
            return cited_only(raw, text)
        except (ValidationError, ValueError, AssertionError):
            continue
    return None

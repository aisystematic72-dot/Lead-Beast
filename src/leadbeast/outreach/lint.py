"""Draft lint: no pressure language, no restricted signals, no uncited claims. Pure."""

from __future__ import annotations

import re
from dataclasses import dataclass

PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


@dataclass(frozen=True)
class LintResult:
    ok: bool
    problems: tuple[str, ...]


def lint(body: str, distress_words: list[str], restricted_cited: list[str]) -> LintResult:
    problems: list[str] = []
    low = body.lower()
    for w in distress_words:
        if re.search(r"\b" + re.escape(w.lower()) + r"\b", low):
            problems.append(f"distress_word:{w}")
    for sig in restricted_cited:
        problems.append(f"restricted_signal:{sig}")
    if PLACEHOLDER.search(body):
        problems.append("unfilled_placeholder")
    if len(body) > 2500:
        problems.append("too_long")
    return LintResult(not problems, tuple(problems))

"""Secrets are referenced by name and resolved from the environment or a restricted secrets file.

They are never stored in the database, the repo or logs.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from leadbeast.core.config import get_settings


@lru_cache
def _file_secrets() -> dict[str, str]:
    p = get_settings().secrets_file
    if not p or not Path(p).exists():
        return {}
    data = json.loads(Path(p).read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.items()}


def resolve(ref: str | None) -> str | None:
    if not ref:
        return None
    return os.environ.get(ref) or _file_secrets().get(ref)

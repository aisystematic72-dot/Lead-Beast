"""Vercel entry point. Vercel's FastAPI preset looks for `app` in a root app.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from leadbeast.api.main import app  # noqa: E402,F401

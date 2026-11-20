"""Procrastinate app on the same PostgreSQL database. No broker."""

from __future__ import annotations

from procrastinate import App, PsycopgConnector

from leadbeast.core.config import get_settings


def _conninfo() -> str:
    url = get_settings().database_url
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


app = App(connector=PsycopgConnector(conninfo=_conninfo()), import_paths=["leadbeast.jobs.tasks"])

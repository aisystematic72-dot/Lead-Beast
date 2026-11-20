"""Operator and cron entry points. Thin wrappers over services; long work is enqueued."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID

import typer

from leadbeast.core.config import get_settings
from leadbeast.core.db import session
from leadbeast.core.ids import new_run_id
from leadbeast.core.logging import configure

app = typer.Typer(help="LeadBeast: cited evidence, explainable scores, human-approved outreach.",
                  no_args_is_help=True)
db_app, pack_app, campaign_app, run_app = (typer.Typer(no_args_is_help=True) for _ in range(4))
golden_app, health_app, export_app, user_app = (typer.Typer(no_args_is_help=True) for _ in range(4))
jobs_app, watch_app, offer_app, problem_app = (typer.Typer(no_args_is_help=True) for _ in range(4))
social_app = typer.Typer(no_args_is_help=True, help="Social media activity checks.")
for name, sub in [("db", db_app), ("pack", pack_app), ("campaign", campaign_app),
                  ("run", run_app), ("golden", golden_app), ("health", health_app),
                  ("export", export_app), ("user", user_app), ("jobs", jobs_app),
                  ("watch", watch_app), ("offer", offer_app), ("problem", problem_app),
                  ("social", social_app)]:
    app.add_typer(sub, name=name)

T = TypeVar("T")


def arun(coro: Awaitable[T]) -> T:
    """asyncio.run with a loop psycopg's async driver supports (Windows needs the selector loop)."""
    import sys

    if sys.platform == "win32":
        return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)  # type: ignore[arg-type]
    return asyncio.run(coro)  # type: ignore[arg-type]


def run(fn: Callable[[], Awaitable[T]]) -> T:
    configure(json=False)
    return arun(fn())


@app.command("serve")
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the web app and API (works on Windows, unlike `uvicorn --reload`)."""
    import uvicorn

    configure()
    config = uvicorn.Config("leadbeast.api.main:app", host=host, port=port, loop="none",
                            proxy_headers=True)
    arun(uvicorn.Server(config).serve())


def out(obj: Any) -> None:
    typer.echo(json.dumps(obj, indent=2, default=str))


# ------------------------------------------------------------------ setup
@db_app.command("migrate")
def db_migrate() -> None:
    """Apply database migrations (and Procrastinate's schema)."""
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), "head")

    async def _q() -> None:
        from leadbeast.jobs.app import app as japp

        async with japp.open_async():
            try:
                await japp.schema_manager.apply_schema_async()
            except Exception as e:  # already applied
                typer.echo(f"procrastinate schema: {e.__class__.__name__} (likely present)")

    run(_q)
    typer.echo("migrated")


@app.command("init")
def init() -> None:
    """Seed connectors and load the default pack."""

    async def _go() -> None:
        from leadbeast.health.service import seed_connectors
        from leadbeast.packs.service import load_pack_from_disk

        async with session() as s:
            n = await seed_connectors(s)
            p = await load_pack_from_disk(s, "visibility_gap")
            typer.echo(f"connectors added: {n}; pack {p.name} v{p.version} loaded")

    run(_go)


@user_app.command("add")
def user_add(username: str, role: str = typer.Option("operator"),
             password: str = typer.Option(..., prompt=True, hide_input=True,
                                          confirmation_prompt=True)) -> None:
    from leadbeast.api.auth import hash_password
    from leadbeast.db import models as m

    async def _go() -> None:
        async with session() as s:
            s.add(m.AppUser(username=username, password_hash=hash_password(password), role=role))
    run(_go)
    typer.echo(f"user {username} ({role}) created")


@user_app.command("token")
def user_token() -> None:
    """Create an API token. Put the printed hash in LB_API_TOKEN_HASH."""
    from leadbeast.api.auth import new_api_token

    tok, h = new_api_token()
    typer.echo(f"token (shown once): {tok}\nLB_API_TOKEN_HASH={h}")


# ------------------------------------------------------------------ packs and offers
@pack_app.command("validate")
def pack_validate(path: Path) -> None:
    from leadbeast.detectors.registry import signals_by_detector
    from leadbeast.packs.loader import load_pack, validate_pack

    spec = load_pack(path)
    problems = validate_pack(spec, signals_by_detector())
    if problems:
        for p in problems:
            typer.secho(f"✗ {p}", fg="red")
        raise typer.Exit(1)
    typer.secho(f"✓ {spec.name} v{spec.version}: {len(spec.all_signals())} signals, "
                f"{len(spec.detectors)} detectors, {len(spec.problems)} problems", fg="green")


@pack_app.command("load")
def pack_load(name: str = typer.Argument("visibility_gap")) -> None:
    """Store the pack from packs/<name> (a new version is added; existing ones are never edited)."""
    from leadbeast.packs.service import load_pack_from_disk

    async def _go() -> str:
        async with session() as s:
            p = await load_pack_from_disk(s, name)
            return f"{p.name} v{p.version} ({p.id})"
    typer.echo(f"loaded {run(_go)}")


@campaign_app.command("use-pack")
def campaign_use_pack(campaign: UUID, version: str, name: str = "visibility_gap") -> None:
    """Point a campaign at a stored pack version (see `pack load`). Rescore afterwards."""
    from sqlalchemy import select

    from leadbeast.db import models as m

    async def _go() -> str:
        async with session() as s:
            camp = await s.get(m.Campaign, campaign)
            pk = await s.scalar(select(m.Pack).where(m.Pack.name == name, m.Pack.version == version))
            if camp is None or pk is None:
                raise typer.BadParameter("campaign or pack version not found (run `pack load`?)")
            camp.pack_id = pk.id
            return f"{camp.name} -> {name} v{version}"
    typer.echo(run(_go))


@problem_app.command("validate")
def problem_validate(path: Path = Path("packs/visibility_gap")) -> None:
    pack_validate(path)


@offer_app.command("create")
def offer_create(path: Path) -> None:
    from leadbeast.intel.payback import payback
    from leadbeast.packs.loader import load_offer
    from leadbeast.packs.service import create_offer

    offer = load_offer(path)
    pb = payback(offer)

    async def _go() -> UUID:
        async with session() as s:
            return (await create_offer(s, offer)).id
    oid = run(_go)
    typer.echo(f"offer {oid} · payback {pb.multiple_low}x–{pb.multiple_high}x ({pb.verdict})")
    if pb.verdict == "fail":
        typer.secho("warning: this offer fails the payback rule (2x)", fg="yellow")


# ------------------------------------------------------------------ campaigns
@campaign_app.command("create")
def campaign_create(offer: UUID = typer.Option(...), pack: str = "visibility_gap",
                    area: str = typer.Option(...), name: str | None = None,
                    budget_cents: int = 0) -> None:
    from leadbeast.packs.service import create_campaign

    async def _go() -> UUID:
        async with session() as s:
            c = await create_campaign(s, name=name or f"{pack} · {area}", offer_id=offer,
                                      pack_name=pack, area=area, budget_cap=budget_cents)
            return c.id
    typer.echo(str(run(_go)))


@campaign_app.command("dry-run")
def campaign_dry_run(campaign: UUID, source: str = "osm", sample: int = 50,
                     path: str | None = None, full_size: int | None = None) -> None:
    """Launch Lab: run stages 1-6 on a sample; show class mix and projected cost."""
    from leadbeast.pipeline import dry_run

    out(run(lambda: dry_run(campaign, source, sample, path, full_size)))


# ------------------------------------------------------------------ pipeline
@run_app.command("collect")
def run_collect(campaign: UUID, source: str = "osm", path: str | None = None,
                limit: int = 500) -> None:
    from leadbeast.jobs.app import app as japp
    from leadbeast.jobs.tasks import collect_source

    async def _go() -> int:
        async with japp.open_async():
            return await collect_source.defer_async(campaign_id=str(campaign), source=source,
                                                    run_id=new_run_id(), path=path, limit=limit)
    typer.echo(f"queued job {run(_go)}")


@run_app.command("detect")
def run_detect(campaign: UUID, only: str | None = None) -> None:
    from sqlalchemy import select

    from leadbeast.db import models as m
    from leadbeast.jobs.app import app as japp
    from leadbeast.jobs.tasks import run_detectors

    async def _go() -> int:
        async with session() as s:
            ids = (await s.scalars(select(m.CampaignBusiness.business_id).where(
                m.CampaignBusiness.campaign_id == campaign))).all()
        rid = new_run_id()
        async with japp.open_async():
            for bid in ids:
                await run_detectors.defer_async(business_id=str(bid), campaign_id=str(campaign),
                                                run_id=rid, only=only.split(",") if only else None)
        return len(ids)
    typer.echo(f"queued detectors for {run(_go)} businesses")


@run_app.command("inline")
def run_inline(business: UUID, campaign: UUID) -> None:
    """Run detect, contact and score for one business now (no queue)."""
    from leadbeast.pipeline import process_business

    typer.echo(run(lambda: process_business(business, campaign, new_run_id())))


@app.command("score")
def score_cmd(campaign: UUID, rescore_all: bool = typer.Option(False, "--rescore-all")) -> None:
    from sqlalchemy import select

    from leadbeast.db import models as m
    from leadbeast.scoring.service import score_business

    async def _go() -> dict[str, int]:
        counts: dict[str, int] = {}
        async with session() as s:
            ids = (await s.scalars(select(m.CampaignBusiness.business_id).where(
                m.CampaignBusiness.campaign_id == campaign))).all()
        for bid in ids:
            async with session() as s:
                snap, _ = await score_business(s, bid, campaign)
                counts[snap.cls] = counts.get(snap.cls, 0) + 1
        return counts
    out(run(_go))


# ------------------------------------------------------------------ golden set
@golden_app.command("import")
def golden_import(path: Path, user: str = "operator") -> None:
    from leadbeast.golden.service import import_labels

    async def _go() -> int:
        async with session() as s:
            return await import_labels(s, path, user)
    typer.echo(f"imported {run(_go)} labels")


@golden_app.command("report")
def golden_report() -> None:
    """Precision per signal; below the threshold a signal is held back from drafts."""
    from leadbeast.golden.service import report

    async def _go() -> list[Any]:
        async with session() as s:
            return await report(s)
    rows = run(_go)
    th = get_settings().golden_precision_threshold
    typer.echo(f"{'signal':28} {'precision':>9} {'recall':>7} {'n':>4}  status (threshold {th:.0%})")
    for r in rows:
        p = f"{r.precision:.0%}" if r.precision is not None else "—"
        rc = f"{r.recall:.0%}" if r.recall is not None else "—"
        typer.echo(f"{r.signal:28} {p:>9} {rc:>7} {r.n_labelled:>4}  "
                   f"{'Live' if r.live else 'Held back'}")


# ------------------------------------------------------------------ health, export, jobs
@health_app.command("probe")
def health_probe(all_: bool = typer.Option(True, "--all")) -> None:
    from leadbeast.health.service import probe_all

    async def _go() -> dict[str, Any]:
        async with session() as s:
            return {k: v.__dict__ for k, v in (await probe_all(s)).items()}
    out(run(_go))


@export_app.command("opportunities")
def export_opps(cls: str | None = typer.Option(None, "--class"), fmt: str = "csv",
                campaign: UUID | None = None, output: Path = Path("opportunities.csv")) -> None:
    import csv

    from leadbeast.market.queries import OpportunityFilter, opportunities

    async def _go() -> list[dict[str, Any]]:
        async with session() as s:
            return await opportunities(s, OpportunityFilter(campaign, cls, limit=100000))
    rows = run(_go)
    if fmt == "json":
        output.write_text(json.dumps(rows, default=str, indent=2), encoding="utf-8")
    else:
        with output.open("w", newline="", encoding="utf-8") as f:
            if rows:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
    typer.echo(f"wrote {len(rows)} rows to {output}")


@jobs_app.command("worker")
def jobs_worker(concurrency: int = 4) -> None:
    from leadbeast.jobs.app import app as japp

    configure()

    async def _w() -> None:
        async with japp.open_async():
            await japp.run_worker_async(concurrency=concurrency)

    arun(_w())


@jobs_app.command("schedule")
def jobs_schedule() -> None:
    """Run a worker that only executes periodic tasks (outbox, refresh, health)."""
    from leadbeast.jobs.app import app as japp

    configure()

    async def _w() -> None:
        async with japp.open_async():
            await japp.run_worker_async(queues=["outbox", "health", "maintenance"], concurrency=2)

    arun(_w())


@watch_app.command("add")
def watch_add(name: str = typer.Option("watch"), industry: list[str] = typer.Option([]),
              area: list[str] = typer.Option([]), aspect: list[str] = typer.Option([]),
              min_pressure: int = 60,
              min_class: str = typer.Option(
                  "B", help="Lowest lead class to alert on: A, B, REVIEW, C or D (all)"),
              rising_only: bool = typer.Option(True, "--rising-only/--any-direction"),
              style: str = typer.Option("instant", help="instant, hourly or daily")) -> None:
    """Create a Panic Alert watch. Alerts go to the in-app inbox (and any linked channel)."""
    from leadbeast.alerts.models import Area, WatchRule
    from leadbeast.db import models as m

    rule = WatchRule(industries=industry, areas=[Area(kind="city", value=a) for a in area],
                     aspects=aspect, min_pressure=min_pressure, min_class=min_class.upper(),
                     rising_only=rising_only)

    async def _go() -> UUID:
        async with session() as s:
            w = m.PanicWatch(name=name, rule=rule.model_dump(), style=style)
            s.add(w)
            await s.flush()
            return w.id
    typer.echo(f"watch {run(_go)}")


@watch_app.command("edit")
def watch_edit(watch: UUID, min_pressure: int | None = None, min_class: str | None = None,
               enabled: bool | None = typer.Option(None, "--enable/--disable")) -> None:
    """Change a watch's threshold, lowest lead class, or switch it on/off."""
    from leadbeast.alerts.models import WatchRule
    from leadbeast.db import models as m

    async def _go() -> dict[str, Any]:
        async with session() as s:
            w = await s.get(m.PanicWatch, watch)
            if w is None:
                raise typer.BadParameter(f"watch {watch} not found")
            rule = WatchRule.model_validate(w.rule)
            upd: dict[str, Any] = {}
            if min_pressure is not None:
                upd["min_pressure"] = min_pressure
            if min_class is not None:
                upd["min_class"] = min_class.upper()
            w.rule = WatchRule.model_validate({**rule.model_dump(), **upd}).model_dump()
            if enabled is not None:
                w.enabled = enabled
            return {"id": str(w.id), "enabled": w.enabled, "rule": w.rule}
    out(run(_go))


@watch_app.command("evaluate")
def watch_evaluate(campaign: UUID) -> None:
    """Check a campaign's businesses against the enabled watches now (uses current pressure)."""
    from sqlalchemy import select

    from leadbeast.alerts.service import evaluate
    from leadbeast.db import models as m

    async def _go() -> int:
        async with session() as s:
            ids = (await s.scalars(select(m.CampaignBusiness.business_id).where(
                m.CampaignBusiness.campaign_id == campaign))).all()
        n = 0
        for bid in ids:
            async with session() as s:
                n += len(await evaluate(s, bid))
        return n
    typer.echo(f"{run(_go)} new alerts")


@watch_app.command("reverify")
def watch_reverify() -> None:
    """Re-mark existing alerts verified/unverified with the current rule."""
    from leadbeast.alerts.service import reverify

    async def _go() -> int:
        async with session() as s:
            return await reverify(s)
    typer.echo(f"{run(_go)} alerts re-marked")


@watch_app.command("list")
def watch_list() -> None:
    from sqlalchemy import select

    from leadbeast.db import models as m

    async def _go() -> list[dict[str, Any]]:
        async with session() as s:
            return [{"id": str(w.id), "name": w.name, "enabled": w.enabled, "rule": w.rule}
                    for w in (await s.scalars(select(m.PanicWatch))).all()]
    out(run(_go))


@watch_app.command("refresh")
def watch_refresh(campaign: UUID) -> None:
    """Recompute problems and pressure for a campaign's businesses."""
    from sqlalchemy import select

    from leadbeast.db import models as m
    from leadbeast.packs.service import pack_for_campaign
    from leadbeast.scoring.pressure_service import refresh_pressure, refresh_problems

    async def _go() -> int:
        async with session() as s:
            ids = (await s.scalars(select(m.CampaignBusiness.business_id).where(
                m.CampaignBusiness.campaign_id == campaign))).all()
        for bid in ids:
            async with session() as s:
                pack = await pack_for_campaign(s, campaign)
                await refresh_problems(s, bid, pack)
                await refresh_pressure(s, bid, pack)
        return len(ids)
    typer.echo(f"refreshed {run(_go)} businesses")


# ------------------------------------------------------------------ social
CHECKLIST = Path("exports/social_and_maps_checklist.csv")
FB_POSTS = Path("exports/facebook_posts.csv")


@social_app.command("fb-template")
def social_fb_template(checklist: Path = CHECKLIST, posts: Path = FB_POSTS) -> None:
    """List every business with a Facebook link in the posts sheet, ready for post dates."""
    from leadbeast.social.fb_service import write_template

    added, kept = write_template(checklist, posts)
    typer.echo(f"{posts}: {added} added, {kept} kept (fill page_status + post_dates)")


@social_app.command("fb-analyze")
def social_fb_analyze(posts: Path = FB_POSTS,
                      output: Path = Path("exports/facebook_activity.csv"),
                      checklist: Path = CHECKLIST,
                      update_checklist: bool = typer.Option(True, "--update-checklist/--no-update-checklist"),
                      ai: bool = typer.Option(True, "--ai/--no-ai", help="Gemini summaries (else rule text)"),
                      concurrency: int = 4) -> None:
    """Compute posting gaps and verdicts, summarise each page, write the results file."""
    from leadbeast.social.fb_service import analyze

    async def _go() -> dict[str, int]:
        model = None
        if ai:
            from leadbeast.ai.gemini import GeminiClient
            model = GeminiClient()
        try:
            return await analyze(posts, output, checklist if update_checklist else None,
                                 model, concurrency)
        finally:
            if model is not None:
                await model.aclose()

    counts = run(_go)
    typer.echo(f"wrote {output}")
    out(counts)


@social_app.command("fb-sync")
def social_fb_sync(results: Path = Path("exports/facebook_activity.csv"),
                   checklist: Path = CHECKLIST) -> None:
    """Copy verdicts + summaries from the results file into the checklist (no model calls)."""
    from leadbeast.social.fb_service import sync_checklist

    typer.echo(f"{checklist}: {sync_checklist(results, checklist)} rows updated")


@social_app.command("fb-import")
def social_fb_import(campaign: UUID, checklist: Path = CHECKLIST,
                     results: Path = Path("exports/facebook_activity.csv"),
                     posts: Path = FB_POSTS) -> None:
    """Import the checklist into a campaign and refresh social evidence + scores on the dashboard."""
    from leadbeast.social.fb_import import import_checklist

    out(run(lambda: import_checklist(campaign, checklist, results=results, posts=posts)))


if __name__ == "__main__":
    app()

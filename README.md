# LeadBeast

A personal, self-hosted system that finds local businesses likely to pay for a service, proves
why with cited evidence, drafts the approach, and **waits for a human to approve** before
anything is sent. Built to the *LeadBeast Developer Specification (Python)*.

## How a lead moves

```
sources ─► collect ─► resolve identity ─► detectors ─► evidence ledger (append-only)
                                                          │
                       score (pure) ◄── fold ◄────────────┘
                          │
       A/B ─► Visibility Audit ─► draft (cited, golden-gated) ─► human approval ─► policy ─► send
```

Each stage is a Procrastinate job on the same PostgreSQL database. Stages talk to each other
through the `event_outbox` table (no broker). Every score can be recomputed from evidence.

## Product rules and where they are enforced

| Rule | Enforced in |
|---|---|
| Offer first | `campaign.offer_id` NOT NULL; `packs/service.create_campaign` |
| Packs are data | `packs/visibility_gap/pack.yaml`, `packs/schema.py` (no weights in code) |
| Vitality gate before score | `scoring/gates.vitality`, first step of `scoring/engine.score` |
| Evidence states, missing ≠ false | `domain/evidence.State`, `evidence/fold.resolve` + property tests |
| Four-part score × confidence | `scoring/parts.py`, `scoring/engine.py` |
| Priority classes A/B/C/D/Review | `scoring/engine.classify` (Review takes precedence) |
| Operating style uses no marketing signals | `detectors/operating_style.py` + test on declared inputs |
| 90% golden precision before a signal appears in an opener | `golden/service.py`, `policy/engine._draft_rules`, `audit/findings.py` |
| Payback ≥ 2× fee, as a range, provisional | `intel/payback.py` |
| Human approval; Class A also needs mystery check | `policy/engine.check("send")` (approval hash must match body) |
| Cited AI extraction (no span, no fact) | `ai/extract.cited_only`, DB check `inferred_needs_span` |
| Append-only ledger | no update/delete code paths; `revoke update, delete` on the app role |

## Run it

```bash
cp .env.example .env              # fill in operator identity, secret key, SMTP
docker compose up -d postgres
docker compose run --rm app leadbeast db migrate
docker compose run --rm app leadbeast init
docker compose run --rm app leadbeast user add admin --role operator
docker compose up -d              # app, worker, scheduler, ntfy, caddy
# optional profiles: --profile ai (Ollama), --profile crm (EspoCRM), --profile reports (Metabase)
```

First campaign:

```bash
leadbeast offer create packs/offers/visibility_starter.yaml        # prints offer id + payback
leadbeast campaign create --offer <offer-id> --area "Brooklyn"
leadbeast campaign dry-run <campaign-id> --source csv --path examples/ny_leads_sample.csv --sample 50
leadbeast run collect <campaign-id> --source osm                   # full run, queued
leadbeast golden import labels.csv && leadbeast golden report      # release signals
```

Then open the web UI: Opportunities → Business 360 → Approvals.

### Local development

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # or .venv/bin/pip
pytest -q                     # unit, property, contract, web
LB_TEST_DATABASE_URL=postgresql+psycopg://lb:lb@localhost:5432/lb_test pytest -m integration
ruff check src tests && lint-imports
uvicorn leadbeast.api.main:app --reload
```

## Layout

```
packs/              ICP packs (YAML + templates) and example offers
alembic/            migrations (tables, evidence_current view, market views, audit trigger)
src/leadbeast/
  core/             config, clock, ids (UUIDv7), logging, polite HTTP fetcher, ports, secrets
  domain/           pydantic models: evidence, business, scoring
  evidence/         fold (pure), facts view, freshness windows, ledger I/O + outbox
  packs/            pack schema, loader/validator, campaign + offer service
  sources/          CSV import (M2/M19), OpenStreetMap Overpass (M2)
  identity/         normalise, block, compare, decide, reversible merge/unmerge (M3)
  detectors/        SDK, registry, 8 built-in detectors, runner (M4)
  ai/               Ollama ModelClient, cited extraction, AI site-facts detector
  contacts/         free contact finder, syntax/MX/role/webmail checks (M5)
  scoring/          gates, parts, engine, gap types, problems & pressure (M6, s.19)
  intel/            payback range, peer sets (M7, M18)
  audit/            findings, Visibility Audit assembler, HTML/PDF report (M16)
  outreach/         compose, lint, channels, draft/approve/send/inbound (M8, M9)
  crm/              EspoCRM + CSV adapters, idempotent hashed sync (M10)
  market/           read models for every screen and Metabase views (M12, M21)
  policy/           policy engine, suppression, decision log, deliverability (M13, s.21)
  cost/             reserve-before-spend budget, three cap levels (M14)
  health/           probes and circuit breaker (M22)
  alerts/           Panic Alerts: matcher, quiet hours, render, notifiers (M24)
  jobs/             Procrastinate tasks, outbox dispatcher, schedules
  api/ web/ cli.py  FastAPI JSON API, Jinja+HTMX UI, Typer CLI
tests/              unit, property (hypothesis), contract (HTML fixtures), integration
```

## What is deliberately not here

- **Map/search scraping.** The listing and rank signals come from your own imported data
  (CSV columns such as `claimed`, `reviews`, `map pack rank`). Map platforms forbid
  automated scraping, and the spec rules out bypassing blocks, so the code never fetches them.
- **Social-activity checks.** Platforms need a login to read activity; the detector records
  `unknown` with the reason instead of guessing.
- **Licence registries.** These differ per state. `licence_active` comes from an imported
  `licence status` column or manual evidence until a state adapter is written.
- **Phase 5 items** (paid contact sources, challenger scoring model, SMS/WhatsApp) are built
  from measured need. The interfaces (`ContactProvider`, `model_version`, `ChannelAdapter`)
  are in place.

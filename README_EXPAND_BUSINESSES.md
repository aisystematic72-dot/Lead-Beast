# Expand the verified business list (LeadBeast)

**How to use this file.** Open a new Claude Code chat in `c:\App` and send the prompt at the end of
this file (section **PROMPT FOR CLAUDE**), with your own values filled in.

---

## 0. Run settings (given by the user in the chat message)

| Setting | Meaning | Required |
|---|---|---|
| `TARGET` | Total number of **active, verified businesses in the whole list** (all states and categories together) after this run. Example: the list has 61, `TARGET = 100` → add 39. | yes |
| `CATEGORY` | Business type(s) to find, e.g. `plumbing`, `dental`, `cafe`, `auto repair`, `hair salon`. Several are allowed, comma-separated. Only businesses of these categories are collected. | yes |
| `STATE` | US state to search, full name, e.g. `California`, `Texas`. | yes |
| `CITY` | City or cities inside `STATE`, comma-separated. If missing or `any`, choose cities yourself (Step 3). | no |
| `BATCH_MAX` | Most new businesses to verify in one chat. Default **50**. | no |

**If `TARGET`, `CATEGORY` or `STATE` is missing from the user's message, ask for it before doing
anything else.** Repeat the settings back to the user in one line, then start.

---

## 1. Your job, Claude

You are working in the LeadBeast project (`c:\App`, Windows, PowerShell). The user keeps a list of
verified small local businesses. Your job:
1. find new businesses of the given `CATEGORY` in the given `STATE` / `CITY`;
2. verify them exactly the way the existing businesses were verified (this file is that method);
3. keep going until the whole list has `TARGET` active businesses;
4. make sure every new business shows a **complete call brief in the Call desk dashboard**
   (`/calls`, Section 12);
5. give the user the updated list and a short report.

### How the dashboard uses your work

The web app (`.\.venv\Scripts\leadbeast.exe serve`, then http://127.0.0.1:8000) shows each
business on several screens. They are all built from the database. **There is nothing to build by
hand:** if the facts are saved in the right format, the screens fill themselves.

| Screen | Shows | Built from |
|---|---|---|
| **Opportunities** / **Business 360** | score, class, evidence | detectors + your saved facts |
| **Call desk → Call list** (`/calls`) | every business ranked High / Medium / Low opportunity | `src/leadbeast/calls/brief.py` (read only) |
| **Call brief** (`/calls/<id>`) | owner, years, contacts, how we know it is open, what we noticed (no website, quiet Facebook, dated site, no booking, no ads, no email list, no fresh content), which service fixes each one, a call script | same |
| **Reachable** | businesses worth contacting now, from Facebook activity | `facebook_activity` facts |
| **Settings → Download CSV** | the full verified list | `export_rows` |

What the call brief reads, so save these carefully:
- **Owner name:** taken from the text of the `licence_active` or `open_status` note, after the
  word `owner`, `qualifier` or `qualifier/owner`, ending at `;` `,` `(` or a URL. Write it as
  `owner John Smith;` (Section 6). Without it the script says "could I speak with the owner?".
- **Years in business** and **staff count:** number facts (`years_in_business`, `staff_count`).
- **"State licence active" / "Checked open":** from `licence_active` / `open_status`.
- **Phone and email:** eligible contacts, best confidence first.
- **Website:** `business.website_url`. A link that is really a Facebook, Instagram, Yelp,
  Linktree or ordering page counts as **no website**.
- **Website findings** (dated, not mobile friendly, no booking or chat, no ads, no email list, no
  fresh content): from the website detectors. They must have run on the **verified** website,
  so re-run them whenever you change the website (Step 11.1).
- **Facebook status and summary:** the `facebook_activity` fact written by `social fb-import`
  (Step 10e). A Facebook link with no activity check shows as "unknown".
- **Wording per trade:** `brief.py` has special wording for plumbing, hvac, electrician,
  roofing, dental and cafe. Other categories use general wording ("your services"). That is
  fine: do not edit the code.

Work through the batch without stopping to ask. Stop and ask only when you are truly blocked:
- the database is down;
- Chrome is not connected for the Facebook step;
- Facebook shows a security checkpoint;
- the chosen city has run out of businesses of that category. Then suggest nearby cities.

### Hard rules (apply to every step)

1. **No code changes.** Do not edit anything under `c:\App\src`, `alembic`, `packs` or `tests`.
   Throw-away helper scripts go in your scratchpad directory, never in the project.
2. **No paid APIs.** Free sources only: OpenStreetMap, official public records and their free
   APIs, the business's own website, your built-in web search, and the user's Chrome (Step 10).
3. **Never delete records.** To reject a business, set `business.status = 'removed'`. It stays
   restorable from Settings. Evidence is append-only: add new facts, never update or delete old ones.
4. **Accept a fact only with a source.** Accept a phone, address or social account only if the
   source shows the business's own address, phone, licence number, or exact name + city.
   Look-alikes in other cities are rejected.
5. **Order of trust:** official records, then the business's own website, then directories, then
   a single listing.
6. **Websites:** do not bypass bot protection, logins, paywalls or robots.txt. Mark the site
   "not readable" and move on. Do not scrape Google Maps or Yelp.
7. **Facebook and Instagram** are read only through the user's own Chrome (Claude in Chrome
   extension), slowly, looking at public business pages only. Never like, follow, comment,
   message, or click anything that opens a dialog.
8. **Stay inside the settings.** Only businesses of `CATEGORY`, located in `STATE` (and in
   `CITY` when given). Drop anything else, even if it looks like a good lead.
9. **Size:** 1 to 25 people. No chains, franchises or institutions.
10. **Save as you go,** after every 10–25 businesses, so a cut-off chat loses little work.
11. Before changing any file in `exports\`, copy it to `<name>.bak-YYYYMMDD`.

---

## 2. Setup checks (do these first)

1. **Python:** use `.\.venv\Scripts\python.exe` and the CLI `.\.venv\Scripts\leadbeast.exe`.
   The database is the local PostgreSQL named in `.env` (`LB_DATABASE_URL`). Do not print `.env`
   values: they contain secrets.
2. **Windows async loop:** any Python script that touches the database must start its event
   loop like this, or psycopg fails with a *ProactorEventLoop* error:
   ```python
   asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
   ```
   The helper's `run()` already does this.
3. **Write the helper module** `lb_helpers.py` (Section 6) into your scratchpad. Import it from
   every script you write.
4. **Find or create the campaign for `STATE`.** There is one campaign per state, named
   `Non-IT small businesses, <STATE>`. California already exists:
   `01a1c709-dacd-73eb-b88c-ab5fd1e23294`. Use `campaign_for_state(STATE)`. If it returns
   `None`, create the campaign:
   ```powershell
   .\.venv\Scripts\leadbeast.exe campaign create --offer 01a1c6b3-e9a9-706e-8f92-15a4580412d8 --area "<STATE>" --name "Non-IT small businesses, <STATE>"
   ```
   The command prints the new campaign id. Call it `CID` from here on.
5. **Read the current state** and print it to the user:
   - total active businesses in the whole list, from `list_size()`;
   - active and removed counts in `CID`;
   - how many active businesses of `CATEGORY` already exist in `STATE` / `CITY`, from
     `cities_in_list(<ST>)`;
   - `NEED = TARGET − total active`.

   If `NEED <= 0`, report and stop. If `NEED > BATCH_MAX`, do `BATCH_MAX` now and tell the user
   to start a new chat for the rest.
6. **Set the file names for this state.** `<st>` = lowercase state name with underscores, e.g.
   `california`, `new_york`.

   | File | California | Any other state |
   |---|---|---|
   | `<CHECKLIST>` | `exports\social_and_maps_checklist.csv` | `exports\social_and_maps_checklist_<st>.csv` |
   | `<POSTS>` | `exports\facebook_posts.csv` | `exports\facebook_posts_<st>.csv` |
   | `<RESULTS>` | `exports\facebook_activity.csv` | `exports\facebook_activity_<st>.csv` |

   California keeps its existing files. **Never mix two states in one checklist:** the Facebook
   import links every row in a checklist to the campaign you name.
7. **Save a "before" snapshot.** Write `exports\before_batch_<N>.csv` with
   `write_export(path)`. `<N>` = the next batch number; check which `batch_*` files already exist.

About 60% of collected businesses survived verification last time (61 of 100). **Collect about
1.8 × NEED candidates.**

---

## 3. Collect candidates (OpenStreetMap, free)

1. **Turn `CATEGORY` into OpenStreetMap tags.** Use the table below. For a category not in the
   table, look up the right tag on the OpenStreetMap wiki (`wiki.openstreetmap.org`, "Map features").
   Use 1–3 tags that match the category closely. Tell the user which tags you used.

   | Category | OSM tags |
   |---|---|
   | hvac | `"craft"="hvac"`, `"shop"="hvac"` |
   | plumbing | `"craft"="plumber"` |
   | roofing | `"craft"="roofer"` |
   | electrician | `"craft"="electrician"` |
   | dental | `"amenity"="dentist"`, `"healthcare"="dentist"` |
   | cafe | `"amenity"="cafe"` |
   | landscaping | `"craft"="gardener"`, `"landuse"="landscaping"` |
   | cleaning | `"shop"="dry_cleaning"`, `"craft"="cleaning"` |
   | auto repair | `"shop"="car_repair"` |
   | hair salon / barber | `"shop"="hairdresser"` |
   | beauty / nail salon | `"shop"="beauty"` |
   | bakery | `"shop"="bakery"` |
   | restaurant | `"amenity"="restaurant"` |
   | florist | `"shop"="florist"` |
   | veterinarian | `"amenity"="veterinary"` |
   | gym / fitness | `"leisure"="fitness_centre"` |
   | carpenter | `"craft"="carpenter"` |
   | painter | `"craft"="painter"` |
   | locksmith | `"shop"="locksmith"`, `"craft"="locksmith"` |
   | pet grooming | `"shop"="pet_grooming"` |
   | chiropractor | `"healthcare"="chiropractor"` |
   | photographer | `"craft"="photographer"` |
   | tailor | `"craft"="tailor"` |

2. **Choose the cities.**
   - If `CITY` is given, use exactly those cities.
   - If not, choose 3–6 cities in `STATE` that are not already well covered: check
     `cities_in_list(<ST>)`. Prefer mid-sized cities.
   - **Never query the whole state:** it is very slow and returns the same first results every
     time.
3. **Query one city at a time** with `collect_osm(city, STATE, category, tags, limit=300)`.
   The city name must match the OpenStreetMap administrative boundary name exactly, e.g.
   "Sacramento" or "Austin".
   - Wait at least 10 seconds between queries.
   - On HTTP 429 or 504, wait 60 seconds and retry once, then skip that city.
   - If a city returns nothing, try the official name (e.g. "City of …") once.
4. **Keep only records that:**
   - have a `name`;
   - have at least one of `phone` / `website` (records with neither are almost impossible to
     verify);
   - are in `STATE`: the `addr:state` tag, when present, must match.
5. **Too few?** If the chosen city gives fewer candidates than needed, tell the user. Offer
   nearby cities in the same state and continue only with the user's answer, unless `CITY = any`.
6. Save everything collected to `exports\batch_<N>_raw.csv`, including the raw OSM tags as JSON.

## 4. De-duplicate and screen (no web calls)

1. **De-duplicate** against **every** business in the database, active *and* removed, so
   rejected businesses never come back. Use `all_businesses()`. A candidate is a duplicate if
   any of these match:
   - phone (E.164);
   - website domain;
   - normalised name + city (lowercase, punctuation stripped, "inc", "llc" and "the" dropped).
2. **Exclude** any candidate that is:
   - a chain or franchise: OSM `brand`, `brand:wikidata` or `operator` tag naming a company, or
     a known national or regional chain name (Starbucks, Peet's, Philz, Blue Bottle,
     Roto-Rooter, Mr. Rooter, Aspen Dental, Western Dental, Great Clips, Supercuts, Jiffy Lube,
     Midas, Meineke, One Hour, Benjamin Franklin, Mister Sparky, etc.);
   - run by a university, college, hospital, clinic network, hotel, airport, school district or
     government;
   - clearly bigger than 25 staff: the company says 100+ employees or 10+ locations;
   - fake or sample data: 555-01xx phone numbers, `.example` emails, names like "Test";
   - not of `CATEGORY`, or not in `STATE` / `CITY`.
3. Write the excluded rows, with a reason, to `exports\batch_<N>_excluded.csv`.
4. Pick the best `ceil(1.8 × NEED)` survivors. When several categories or cities were given,
   spread the picks across them. Write them to `exports\batch_<N>_candidates.csv` with exactly
   these columns (the CSV importer recognises them):
   `name,trade,phone,email,website,address,city,state,zip,lat,lon`.
   - `trade` = the category as the user wrote it, lowercase.
   - `address` = the street line only.
   - `state` = the two-letter code, e.g. `CA`, `TX`.
   - phones in E.164 (`+1XXXXXXXXXX`).

## 5. Import into the campaign

1. Record `since = datetime.now(timezone.utc)` just before running:
   ```powershell
   .\.venv\Scripts\leadbeast.exe campaign dry-run <CID> --source csv --path exports\batch_<N>_candidates.csv --sample <number of rows>
   ```
   This runs inline, so no worker is needed. It:
   - imports the rows;
   - merges any duplicates the identity resolver finds;
   - runs the website detectors, finds free contacts, and scores each business.
2. List the **new** business ids with `new_businesses(CID, since)` and print the count. If it is
   much lower than the row count, the resolver merged some rows into existing businesses: those
   are fine, do not verify them again.

---

## 6. Helper module (`lb_helpers.py`, scratchpad only)

```python
"""Helpers for the expand-businesses procedure. Lives in the scratchpad, not in c:\\App."""
import asyncio
import csv
import sys
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text

from leadbeast.core.db import session
from leadbeast.core.ports import SourceQuery
from leadbeast.db import models as m
from leadbeast.evidence.ledger import append, manual_event
from leadbeast.market.queries import EXPORT_COLUMNS, export_rows
from leadbeast.sources import osm

OFFER = UUID("01a1c6b3-e9a9-706e-8f92-15a4580412d8")


def run(coro):
    if sys.platform == "win32":
        return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(coro)


# ---------------------------------------------------------------- campaigns and list
async def campaign_for_state(state: str) -> UUID | None:
    async with session() as s:
        return await s.scalar(text(
            "select id from campaign where name = :n order by created_at limit 1"),
            {"n": f"Non-IT small businesses, {state}"})


async def list_size() -> int:
    """Active businesses in the whole list (what TARGET counts)."""
    async with session() as s:
        return await s.scalar(text("""
            select count(distinct b.id) from business b
            join campaign_business cb on cb.business_id = b.id
            join campaign c on c.id = cb.campaign_id
            where b.status = 'active' and c.name like 'Non-IT small businesses, %'"""))


async def cities_in_list(state_code: str) -> list[tuple]:
    """(city, trade, active count) already in the list, for a two-letter state code."""
    async with session() as s:
        return [tuple(r) for r in (await s.execute(text("""
            select address->>'city', trade, count(*) from business
            where status = 'active' and address->>'state' = :st
            group by 1, 2 order by 3 desc"""), {"st": state_code})).all()]


async def all_businesses() -> list[dict]:
    async with session() as s:
        rows = (await s.execute(text(
            "select id, canonical_name, phone_e164, domain, address->>'city' as city, "
            "address->>'state' as state, trade, status from business"))).mappings().all()
        return [dict(r) for r in rows]


async def new_businesses(cid, since: datetime) -> list[dict]:
    async with session() as s:
        rows = (await s.execute(text(
            "select b.id, b.canonical_name, b.trade, b.phone_e164, b.website_url, b.address "
            "from business b join campaign_business cb on cb.business_id = b.id "
            "where cb.campaign_id = :c and b.created_at >= :t order by b.created_at"),
            {"c": UUID(str(cid)), "t": since})).mappings().all()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------- collection
async def collect_osm(city: str, state: str, category: str, tags: list[str],
                      limit: int = 300) -> list[dict]:
    """One Overpass query for one city. `tags` like ['"shop"="car_repair"'].
    The category is registered in memory only (no code change) so records get trade=category."""
    osm.TRADE_TAGS[category] = tags
    out = []
    q = SourceQuery(area=city, trades=[category], limit=limit)
    async for r in osm.OsmAdapter().fetch(q):
        p = r.payload
        p["query_city"], p["query_state"] = city, state
        out.append(p)
    return out


# ---------------------------------------------------------------- saving facts
async def save_fact(bid, signal: str, value, note: str, url: str | None = None) -> None:
    """Append one operator-verified fact. ALWAYS put the source URL in `note` too."""
    async with session() as s:
        ev = manual_event(UUID(str(bid)), signal, value, "research", note=note)
        if url:
            ev = ev.model_copy(update={"source_url": url})
        await append(s, [ev], recorded_by="research")


async def set_status(bid, status: str) -> None:  # "removed" or "active"
    async with session() as s:
        b = await s.get(m.Business, UUID(str(bid)))
        b.status = status


async def update_business(bid, *, line1=None, city=None, state=None, postcode=None,
                          phone=None, website=None, verified_source=None) -> None:
    async with session() as s:
        b = await s.get(m.Business, UUID(str(bid)))
        a = dict(b.address or {})
        for k, v in (("line1", line1), ("city", city), ("state", state),
                     ("postcode", postcode), ("verified_source", verified_source)):
            if v:
                a[k] = v
        a.setdefault("country", "US")
        b.address = a  # assign a new dict so the change is saved
        if postcode:
            b.postcode = postcode
        if phone:
            b.phone_e164 = phone
        if website:
            b.website_url = website


async def upsert_contact(bid, *, phone=None, email=None, confidence="low", eligible=True,
                         source_url=None, name=None, role=None) -> None:
    """confidence: high = Verified, medium = Corroborated, low = Single source.
    eligible=False disables a junk or contradicted contact."""
    bid = UUID(str(bid))
    async with session() as s:
        q = select(m.Contact).where(m.Contact.business_id == bid)
        q = q.where(m.Contact.phone_e164 == phone) if phone else q.where(m.Contact.email == email)
        c = (await s.scalars(q)).first()
        if c is None:
            c = m.Contact(business_id=bid, phone_e164=phone, email=email,
                          channel_pref="phone" if phone else "email")
            s.add(c)
        c.confidence, c.eligible = confidence, eligible
        c.source_url = source_url or c.source_url
        c.name, c.role = name or c.name, role or c.role


async def contacts_of(bid) -> list[dict]:
    async with session() as s:
        rows = (await s.scalars(select(m.Contact).where(
            m.Contact.business_id == UUID(str(bid))))).all()
        return [{"phone": c.phone_e164, "email": c.email, "confidence": c.confidence,
                 "eligible": c.eligible, "source": c.source_url} for c in rows]


# ---------------------------------------------------------------- call desk dashboard
async def brief_check(ids=None) -> list[dict]:
    """Call brief for each business (or only `ids`), with anything missing. Read only."""
    from leadbeast.calls.brief import call_list
    async with session() as s:
        rows = await call_list(s, "")
    want = {str(i) for i in ids} if ids else None
    out = []
    for b in rows:
        if want and str(b.id) not in want:
            continue
        missing = [k for k, ok in (
            ("phone or email", b.phone or b.email),
            ("open/licence proof", b.verified != "Not confirmed"),
            ("facebook activity", b.fb_status != "unknown"),
            ("website detectors", not any("could not check" in t for t in b.tips)),
        ) if not ok]
        out.append({"id": str(b.id), "name": b.name, "level": b.level,
                    "owner": b.owner or "-", "years": b.years, "verified": b.verified,
                    "facebook": b.fb_status, "findings": [x.title for x in b.findings],
                    "missing": missing})
    return out


# ---------------------------------------------------------------- export
async def write_export(path: str, cid=None) -> int:
    """cid=None writes the whole list (all campaigns); pass a campaign id for one state."""
    async with session() as s:
        rows = await export_rows(s, UUID(str(cid)) if cid else None)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(EXPORT_COLUMNS)
        w.writerows(rows)
    return len(rows)
```

### How facts are saved (copy these formats exactly)

| What | Call |
|---|---|
| Business is open | `save_fact(bid, "open_status", True, "Own website live 2026-11-19 https://… ; owner Maria Lopez; NPI 1234567890 active")` |
| Licence active | `save_fact(bid, "licence_active", True, "CSLB #803591 Current-Active, qualifier/owner John Smith; exp 2027-05-31 https://www.cslb.ca.gov/803591")`. This note appears in the export's *Licence / verification* column, and the call brief takes the owner name from it. |
| Closed | `save_fact(bid, "closure_signals", True, "Closed per <source> https://…")`, then `set_status(bid, "removed")` |
| Years in business | `save_fact(bid, "years_in_business", 16, "Since 2010: licence issue date https://…")` |
| Staff count (when known) | `save_fact(bid, "staff_count", 3, "Workers' comp exempt (no employees) https://…")` |
| Phone checked | `save_fact(bid, "contact_phone_valid", True, "Verified (own website / official record): https://…")` |
| Email checked | `save_fact(bid, "contact_email_valid", True, "Corroborated (2 sources): https://… ; https://…")` |
| Social accounts | `save_fact(bid, "social_profiles", {"facebook": url_or_None, "instagram": url_or_None, "other": [...], "checked": "how it was matched"}, "Social links: <how matched> <source url>")` |

Contacts shown in the export and the call brief come from the `contact` table: use
`upsert_contact` (Step 8).

**Owner name rule:** whenever an official record or the website names the owner, write
`owner <First Last>;` (or `qualifier/owner <First Last>;`) inside the `licence_active` or
`open_status` note, exactly like the examples above. The call brief reads it from there.

**Website rule:** if verification finds the real website differs from what OpenStreetMap had,
call `update_business(bid, website="https://…")`. If the only link is a Facebook, Instagram, Yelp
or ordering page, leave it: the brief already treats that as "no website".

---

## 7. Verify each new business (website, existence, address)

Process the new businesses in groups of 10–25. For each one:

### 7a. Business website
Skip social and ordering-page URLs.
1. Read the homepage, plus the contact/about page if needed. Record phone, email, street address,
   owner, founding year, licence number, and links to Facebook, Instagram, YouTube, TikTok, X and
   LinkedIn.
2. Note websites that are dead, for sale, redirect to unrelated content (casino, spam), or show a
   different business. A dead website is a warning sign, not proof of closure.
3. Website age:
   - first Wayback capture: `https://archive.org/wayback/available?url=<domain>&timestamp=19900101`
   - domain registration: `https://rdap.org/domain/<domain>`

### 7b. Existence check
Use the first source that applies to the category and `STATE`:
- **Licensed trades** (contractors: plumbing, HVAC, electrical, roofing, painting, landscaping,
  locksmith; also cosmetology, auto repair or similar where the state licenses them):
  - **California:** CSLB record `https://www.cslb.ca.gov/<LICENCE_NUMBER>` for contractors.
    Other licences are at `search.dca.ca.gov` (Barbering & Cosmetology, Automotive Repair BAR,
    etc.).
  - **Other states:** find the state's official licence lookup. Web search
    `"<STATE> <category> license lookup"` and use only the state government (`.gov`) site,
    e.g. Texas TDLR, Florida DBPR, New York DOS.
  - Find the licence number on the website or by searching `"<name> <city> license"`. Accept
    only **current / active**.
  - Record: legal name, owner/qualifier, address, issue and expiry dates, workers' comp where
    shown (exempt = no employees).
  - Expired, suspended or not found = not verified.
- **Health professionals** (dentists, chiropractors, vets, etc.):
  - Find the NPI (national, all states):
    `https://npiregistry.cms.hhs.gov/api/?version=2.1&organization_name=<name>&state=<ST>`
    (or `&number=<NPI>`).
  - Then check the state's licensing board. California dentists:
    `https://search.dca.ca.gov/profile/440/4401/<LICENCE>/DDS`.
  - Vets have no NPI: use the state veterinary board lookup.
- **Food businesses** (cafes, bakeries, restaurants):
  - county or city health inspection open data (many counties publish it, e.g. Santa Clara
    `data.sccgov.org`, San Francisco `data.sfgov.org` dataset tvy3-wexg);
  - the city business licence registry (e.g. DataSF g8m3-pdis).
  - A passed inspection within 18 months = verified open. No current permit under the name =
    likely closed.
- **Otherwise:** web search for evidence from the last 12–18 months (dated reviews, local guides,
  news, a live website with current hours). Also search `"<name> <address> closed"` to catch
  closures.

Result for each business:

| Result | What to do |
|---|---|
| **Verified open** | official record, or own website plus recent dated evidence |
| **Likely open** | directories only |
| **Unconfirmed** | replace it if candidates remain |
| **Closed** | remove |
| **Not found** | remove |

Save with `open_status` / `licence_active` / `closure_signals` (Section 6).

### 7c. Address
Set street, city, state and ZIP from the strongest source: licence or permit first, then own
website, then directories. Fix them with `update_business(...)`, putting the source in
`verified_source`. If the verified address is outside `STATE` / `CITY`, remove the business.
Note when the licence address is a home or PO box but the website shows an office.

---

## 8. Contact details

1. Look at the current contacts with `contacts_of(bid)`. The detectors already found some.
2. Phones and emails, in order of preference:
   - licence or permit record;
   - own website;
   - directories. Accept a directory number only if two independent sources agree.
3. Label each contact with `upsert_contact`:
   - `confidence="high"` = **Verified** (official record or own website);
   - `"medium"` = **Corroborated** (2+ independent sources);
   - `"low"` = **Single source**.
4. Disable junk with `eligible=False`:
   - template placeholders (user@domain.com);
   - payment-processor emails;
   - numbers contradicted by 2+ sources;
   - numbers with the wrong area code for the city.
5. Save `contact_phone_valid` / `contact_email_valid` facts with the level and source in the note.
6. **A business with no usable contact after this step is removed.**

---

## 9. Social media: find and verify accounts

### 9a. Find accounts
1. Use links found on the website (Step 7a). Ignore share buttons and platform links like
   `facebook.com/2008/fbml`, `facebook.com/sharer`, `instagram.com/` with no handle.
2. If there is still no Facebook or Instagram, use web search:
   `site:facebook.com "<name>" <city>` and `site:instagram.com "<name>" <city>`.

### 9b. Verify each account
Open the profile in Chrome (Section 10 explains how to use Chrome) and read the intro/bio.
1. **Accept** only if it shows the business's own address, phone, website, licence number, or
   exact name + city. **Reject** look-alikes and same-name businesses in other cities.
2. Save with `save_fact(bid, "social_profiles", {...})`. The `checked` text says how it matched,
   e.g. `"FB via search (confirmed: phone matches)"` or `"links read from website"`.
3. Businesses with **no website and no social accounts** are the strongest leads. Keep them if
   they pass existence and contact checks, and list them separately in the report.

---

## 10. Facebook activity: is the business active?

This uses the **Claude in Chrome** extension in the user's own Chrome, which is logged in to
Facebook. Use this state's file names from Step 2.6: `<CHECKLIST>`, `<POSTS>`, `<RESULTS>`.

### 10a. Before starting
1. Load the Chrome tools in one ToolSearch call:
   `select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__get_page_text,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__find,mcp__claude-in-chrome__tabs_close_mcp`
2. Call `tabs_context_mcp` first, then **create a new tab**. Never reuse the user's tabs.
3. If the Chrome tools are missing or disconnected, **stop and ask the user** to open Chrome and
   connect the Claude extension. Do not guess activity.

### 10b. Prepare the sheets
1. Back up `<CHECKLIST>` and `<POSTS>` if they exist.
2. **Append** one row per new *verified* business to `<CHECKLIST>`. Keep existing rows; create
   the file if this is a new state.
   - Use the business's **exact name, phone and address as stored in the database**. The import
     in 10e matches rows by these, and a mismatch creates a duplicate.
   - Columns: `name, trade, phone, address, city, state, zip, website, facebook, instagram,
     other social, social links found how, google maps link, reviews, rating, claimed,
     map pack rank, last post date, followers, notes, facebook activity, facebook summary`.
   - The Maps link is `https://www.google.com/maps/search/?api=1&query=<name+street+city+ST>`.
   - Leave `reviews` through `facebook summary` empty for now.
3. Run:
   ```powershell
   .\.venv\Scripts\leadbeast.exe social fb-template --checklist <CHECKLIST> --posts <POSTS>
   ```
   This adds every business with a Facebook link to `<POSTS>` as `page_status=pending` and keeps
   rows already collected.

### 10c. Read post dates (one page at a time)
Do this for each `pending` row in `<POSTS>`:
1. Navigate the new tab to the page URL. Wait for it to load.
2. If Facebook shows a login wall, a "confirm it's you" or checkpoint screen, or a warning about
   unusual activity: **stop immediately**, tell the user, and wait.
3. If the page says "This content isn't available" or it is removed, set
   `page_status = unavailable`.
4. Otherwise set `page_status = ok`. Read the Intro section to re-confirm it is the right
   business. Then scroll the Posts feed and collect the **dates of the 5 most recent posts**.
   - **Skip the pinned post** (marked "Pinned" or with a pin icon). It is out of date order.
   - Shared posts, photos, videos and reels all count as posts.
   - Get the full date from the timestamp's tooltip or aria-label where possible. Otherwise
     convert relative times using today's date: "3d" = 3 days ago, "2w" = 14 days ago,
     "1y" = about 365 days ago. A date with no year (e.g. "March 4") is the current year, unless
     that would be in the future, in which case it is last year.
   - If the page has fewer than 5 posts, record what is there. The rules treat fewer than 5 as
     inactive.
   - Note the follower count if shown.
5. Write into the row:
   - `post_dates` = `YYYY-MM-DD; YYYY-MM-DD; …`, newest first;
   - `collected_on` = today (`YYYY-MM-DD`);
   - `collector_note` = anything unusual, e.g. "pinned post skipped", "dates from relative
     times", "page name differs, phone matches".
6. Put the follower count in the checklist `followers` column.
7. **Manners:** pause a few seconds between pages, and do no more than about 50 pages per chat.
   Never like, follow, message, comment, accept cookie or other dialogs, or click buttons that
   open popups. Close your tab when done.

### 10d. Judge activity (rules plus Gemini summary)
```powershell
.\.venv\Scripts\leadbeast.exe social fb-analyze --posts <POSTS> --output <RESULTS> --checklist <CHECKLIST>
```
For every row, this computes the gaps between the last 5 posts and the verdict, using the rules
in `src/leadbeast/social/fb_activity.py`:

| Verdict | Rule |
|---|---|
| **inactive** | fewer than 5 posts, or the last post is more than 60 days old |
| **active** | average gap ≤ 14 days **and** last post ≤ 28 days ago |
| **irregular** | average gap ≤ 14 days but a silence since, or average gap > 14 days |
| **unavailable** | page missing or removed |

Gemini then writes a 2-sentence plain-English summary; inactive pages get a rule-based sentence.
The command fills `last post date`, `facebook activity` and `facebook summary` in `<CHECKLIST>`.
Use `--no-ai` only if Gemini fails.

### 10e. Push into the dashboard
```powershell
.\.venv\Scripts\leadbeast.exe social fb-import <CID> --checklist <CHECKLIST> --results <RESULTS> --posts <POSTS>
```
This records the Facebook links and activity details as evidence and rescores. Afterwards, check
that the campaign's active count did not jump unexpectedly. If a checklist row created a
**duplicate** business, set the duplicate to `removed` and fix the row's name, phone or address
to match the database.

---

## 11. Final check, top-up, and rescore

1. **Re-run the website detectors** for every new business whose website you changed or added
   in Step 7. This also refreshes contacts and the score:
   ```powershell
   .\.venv\Scripts\leadbeast.exe run inline <business-id> <CID>
   ```
   Then rescore the campaign:
   ```powershell
   .\.venv\Scripts\leadbeast.exe score <CID> --rescore-all
   ```
2. **Final check.** Every active new business must have:
   - a street address and city inside `STATE` / `CITY`;
   - category = `CATEGORY`;
   - at least one eligible contact;
   - an existence proof (`open_status` or `licence_active` fact from `research`).

   Fix or remove anything that fails, then check again until there are **0 problems**.
3. **Top-up.** If `list_size()` is still below `TARGET` and you are under `BATCH_MAX`, take the
   next candidates from `batch_<N>_candidates.csv` (or collect another city when allowed) and
   repeat Steps 5–11 for them only.

## 12. Call desk dashboard check

1. Run `brief_check(<ids of the new active businesses>)`.
2. For every business with something in `missing`, fix it with the step that saves that fact,
   then run `brief_check` again:
   - **phone or email:** Step 8;
   - **open/licence proof:** Step 7b;
   - **facebook activity:** Steps 10c–10e;
   - **website detectors:** Step 11.1. If the site blocks automated checks, leave it; the brief
     tells the caller to look at the site first.
3. **Owner:** if `owner` is `-` and an official record or the website names the owner, add a new
   `licence_active` or `open_status` fact with `owner <Name>;` in the note. Do not invent names.
   If no owner is named anywhere, leave it.
4. Open a few briefs to confirm they read well. Start the app with
   `.\.venv\Scripts\leadbeast.exe serve` and visit `http://127.0.0.1:8000/calls` and
   `http://127.0.0.1:8000/calls/<id>`, in Chrome if it is connected. Look for:
   - "What we noticed" lists only real findings;
   - the phone number is right;
   - the Facebook line matches what you saw.

   Stop the server when done.

## 13. Deliver

1. Write the final list:
   - `write_export("exports\\final_<total>_verified.csv")`, the whole list across all states,
     e.g. `final_100_verified.csv`;
   - `exports\batch_<N>_removed.csv` with name, city, trade and the reason for every business
     removed in this batch.
2. Tell the user they can also download the list from the dashboard (Settings → Download CSV, or
   `/export/businesses.csv`) after starting it with `.\.venv\Scripts\leadbeast.exe serve`.
3. Give a short report:
   - **settings used:** TARGET, CATEGORY (and the OSM tags used), STATE, cities;
   - **counts:** collected, duplicates skipped, excluded (by reason), closed or not found, added,
     final total;
   - **existence proof by type:** state licence, NPI / health board, health permit, own website
     plus recent reviews;
   - **contact levels:** Verified / Corroborated / Single source;
   - **website and social:** with Facebook, with Instagram, with neither; Facebook verdicts
     (active / irregular / inactive / unavailable);
   - **strongest leads:** businesses with no website and no social accounts;
   - **call desk:** new businesses by opportunity level (High / Medium / Low), how many have an
     owner name, and the top 5 new High businesses to call first (name, city, phone, top finding);
   - **anything not checked,** and why (site not readable, Chrome disconnected, city ran out of
     businesses, etc.);
   - **what to do next:** e.g. "run this guide again with TARGET = 200".

---

## PROMPT FOR CLAUDE

Copy this into a new Claude Code chat opened in `c:\App`, and change the four values:

```
Read c:\App\README_EXPAND_BUSINESSES.md and follow it exactly.
TARGET = 100
CATEGORY = plumbing
STATE = California
CITY = Sacramento
```

- `CATEGORY`: one or more, comma-separated, e.g. `plumbing, hvac`.
- `CITY`: one or more, comma-separated, or `any` to let Claude choose cities in the state.

# Business verification prompt (LeadBeast)

You are working in the LeadBeast project (c:\App). I have a batch of businesses collected from
OpenStreetMap into campaign `<CAMPAIGN_ID>`. Verify every business in the batch and finish with a
clean list where each business is confirmed to exist at its address and has verified contact
details, website and social accounts. Work through the whole batch without stopping to ask; report
at the end.

## Rules (apply to every step)

- Never scrape Google Maps, Yelp, Facebook or Instagram pages and never log in to any platform.
  Use web search results, official records and the business's own website only. Do not bypass
  bot protection, robots.txt or blocked sites: mark them "not readable" and move on.
- Accept a fact only with a source. Accept a social account or phone only if the source shows the
  business's own address, phone, licence number or exact name + city. Look-alikes in other cities
  are rejected.
- Never delete records. Hide rejected businesses with `status = 'removed'` (they stay restorable in
  Settings). Save facts with `manual_event(...)` and `append(...)` so the source link is kept.
- Work in batches of 25–50 and save after each batch, so progress survives interruptions.
- Prefer official records over directories, and directories over a single listing.

## Step 1. Sort the batch (no web calls)

1. Export all businesses in the campaign with name, trade, address, phone, website, coordinates
   and raw OSM tags.
2. Mark as **Excluded** (status `removed`, reason recorded) any business that is:
   - a chain or franchise (OSM `brand`/`operator` tag, or a known chain name),
   - run by a university, hospital, hotel or government,
   - clearly larger than 25 staff (e.g. company says 100+ employees or 10+ locations).
3. Flag fake or sample data (555-01xx phone numbers, `.example` emails) and remove it.

## Step 2. Business website

For each remaining business with a website (skip social and ordering-page URLs):
1. Read the homepage (and contact page if needed). Record phone, email, street address, owner,
   founding year, licence number, and Facebook/Instagram/YouTube/TikTok/X/LinkedIn links.
2. Mark websites that are dead, for sale, redirected to unrelated content (e.g. casino) or show
   another business. A dead website is a warning sign, not proof of closure.
3. Record website age: first Wayback Machine capture and RDAP domain registration date.

## Step 3. Existence check (official records first)

Confirm the business is operating at its address, using the first source that applies:
- **Contractors (plumbing, HVAC, electrical, roofing, etc.):** CSLB record at
  `https://www.cslb.ca.gov/<LICENCE_NUMBER>`. Find the number on the website or by web search
  ("<name> <city> CSLB license"). Accept only "current and active". Record legal name, owner
  (qualifier), address, phone, issue/expiry date, workers' comp (exempt = no employees).
  Expired, suspended or not found = not verified.
- **Dentists / medical:** Dental Board via `search.dca.ca.gov/profile/440/4401/<LICENCE>/DDS`,
  using the licence number from NPPES `npiregistry.cms.hhs.gov/api/?version=2.1&number=<NPI>`.
- **Food businesses:** county health inspection open data (Santa Clara `data.sccgov.org`,
  San Francisco `data.sfgov.org` tvy3-wexg) and city business registries (DataSF g8m3-pdis).
  A passed inspection within 18 months = verified open. No current permit under the name = likely
  closed.
- **Otherwise:** web search for 2025–2026 evidence (dated reviews, guides, news, live website with
  current hours). Search "<name> <address> closed" to catch closures.
Result per business: **Verified open**, **Likely open** (directories only), **Unconfirmed**,
**Closed** or **Not found**. Remove Closed and Not found; replace Unconfirmed if a replacement step
is requested.

## Step 4. Address

Set the street address, city and ZIP from the strongest source (licence record or permit, then
own website, then directories). Fix wrong numbers and fill missing streets. Note when the licence
address is a home or PO box and the website shows a different office.

## Step 5. Contact details

1. Phones and emails, in order of preference: licence or permit record, own website, then
   directories (accept a directory number only if two independent sources agree).
2. Label each contact: **Verified** (official record or own website), **Corroborated**
   (2+ independent sources), **Single source**.
3. Disable junk: template placeholders (user@domain.com), payment-processor emails, numbers
   contradicted by 2+ sources, numbers with the wrong area code for the city.
4. A business with no usable contact after this step is removed.

## Step 6. Social accounts

1. Use links found on the website (Step 2).
2. For businesses with no Facebook or Instagram yet, run a web search restricted to
   `facebook.com` and `instagram.com` with "<name> <city>". Accept only profiles showing the
   business's address, phone, licence number or exact name + city.
3. Save each account with how it was matched (signal `social_profiles`, source "web").
4. Do not read post dates by logging in; leave last post date and followers for the manual
   checklist.

## Step 7. Save, score, audit

1. Save every fact with its source link, then rescore:
   `leadbeast score <CAMPAIGN_ID> --rescore-all`.
2. Audit every visible business. Each must have: street address, city, at least one usable
   contact, and an existence proof. Fix or remove anything that fails, then re-audit until there
   are 0 problems.
3. Build the manual checklist `exports/social_and_maps_checklist.csv` (one row per business, with
   Google Maps link, Facebook, Instagram and empty columns: reviews, rating, claimed,
   map pack rank, last post date, followers, notes).

## Final output

1. The dashboard **Download CSV** (`/export/businesses.csv`) must contain for every business:
   Name, Trade, Street, City, State, ZIP, Phone, Other phones, Email, Website, Facebook,
   Instagram, Other social, Social activity, Contact level, Class, Score, Licence / verification.
2. A short report:
   - counts: collected, excluded (by reason), closed/not found, final verified list;
   - existence proof by type (state licence, county permit, own website + recent reviews);
   - contact levels (verified / corroborated / single source);
   - website and social counts (with Facebook, with Instagram, with neither);
   - businesses with no website and no social accounts (strongest leads);
   - anything that could not be checked and why.

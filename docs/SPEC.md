# Charlotte Metro Food Grades — Requirements Spec

**Owner:** Kyle
**Purpose:** Portfolio showcase — an interactive map of food-service inspection grades across Charlotte/Mecklenburg County, backed by scraped public data, plus a live MCP server exposing the same data.
**Hand-off target:** Claude Fable 5

Because Fable works more autonomously with fewer natural check-in points, resolve §10 (Open decisions) *before* starting the build session rather than mid-build — there's less opportunity to course-correct partway through than there would be in a more interactive session.

---

## 1. Goal

Two deliverables sharing one data pipeline:
1. A public, static, map-first website showing food-service inspection grades across Mecklenburg County.
2. A live MCP server over the same dataset that you (and friends) can query directly.

Both should be portfolio-quality — this is a "look what I built" piece, not just a personal tool.

---

## 2. Data sources

### 2a. Core dataset (Phase 1): Mecklenburg County food & facility inspections

- **Confirmed primary source:** `public.cdpehs.com` — this is the CDPEHS vendor system Mecklenburg's Environmental Health division uses for its public inspection lookup (county-filtered via a query param, e.g. `ESTTST_CTY=60` for Mecklenburg). This is almost certainly what you were calling "NC CDP" from our earlier conversation.
- **Check before committing to it:** NC also runs a newer statewide lookup at `ehids.eh.ncdhhs.gov/eh-lookup`. Verify whether this is a newer system that supersedes CDPEHS, a parallel front-end over the same underlying data, or something narrower — NC's environmental health data infrastructure has migrated before. Pick whichever is more current/complete, and note which one you picked and why.
- **Before writing an HTML scraper against either site:** check whether the page calls a JSON/XML API under the hood (inspect Network tab for XHR calls) or offers a bulk export/download option. These look like ASP.NET WebForms pages (postback-driven grids), which often have an underlying data endpoint that's far more robust to scrape than parsing rendered HTML tables — the latter breaks on any UI tweak.
- **Scope beyond "restaurants":** Mecklenburg's Food & Facilities Sanitation program inspects more than restaurants — meat markets, hotels/motels, mobile food units & pushcarts, temporary food events, child care centers, and schools all go through the same program, likely filterable by facility type in the same system. Pull the full facility-type list during scraping even if Phase 1 only *displays* restaurants — it costs nothing extra now and makes Phase 2 close to free.

### 2b. Expansion sources (Phase 2 — "scrape more public data")

Two portals worth layering in as additional map layers, both already on **ArcGIS Hub** — meaning clean CSV/GeoJSON/REST access is likely available directly, so check for a real API/export before writing any scraping code against these:

- **City of Charlotte Open Data Portal** (`data.charlottenc.gov`) — includes geocoded Code Enforcement Cases among other layers. A natural "neighborhood civic health" companion to food grades.
- **Mecklenburg County GIS / Quality of Life Explorer** (`data.mecknc.gov`, `gis.mecknc.gov`) — 80+ neighborhood-level variables (demographics, income, crime, health, education). Useful as a contextual choropleth layer underneath the restaurant points, not as a primary dataset.

Treat both as optional context layers for Phase 2/3 — don't let them block getting the core food-grade map shipped.

---

## 3. Data model (DuckDB)

```
facilities
  id             text primary key   -- source system's facility ID
  name           text
  facility_type  text               -- restaurant, hotel, mobile_food, child_care, etc.
  address        text
  city           text
  zip            text
  lat            double
  lon            double
  permit_status  text

inspections
  id               text primary key
  facility_id      text references facilities(id)
  inspection_date  date
  score            double
  grade            text             -- derived: A/B/C per NC thresholds (verify current exact
                                     -- cutoffs against state code — historically ~90/80/70 — 
                                     -- don't hardcode without checking, these are state-set)
  inspection_type  text             -- routine, complaint, reinspection, opening
  source_url       text

violations
  id              text primary key
  inspection_id   text references inspections(id)
  code            text
  description     text
  category        text             -- critical / non-critical
  points_deducted double
```

Geocode at ingestion time if lat/lon isn't already provided by the source (addresses should be clean enough for a standard geocoder given they're government permit addresses).

---

## 4. Ingestion pipeline

- **Cadence:** weekly is plenty. Mecklenburg runs ~13,000 inspections/year across ~4,400 facilities — roughly 3 inspections per facility per year on average. Nothing here needs to be near-real-time.
- **Resilience:** wrap each source/facility-type fetch independently; one failure shouldn't take down the whole refresh.
- **History:** keep every inspection record, not just the latest — the trend view in §5 and the leaderboard stretch goal in §9 both depend on having history, not just current state.
- **Provenance:** stamp every record with `fetched_at` so the site can show "last updated" per facility (see §8).

---

## 5. Website (map)

- **MapLibre GL JS**, full-bleed map as the primary UI — this is the centerpiece, not a sidebar feature.
- **Custom map style**, not the default look — this is a portfolio piece, it should read as intentionally designed, not a stock Leaflet/Bootstrap template.
- **Markers:** clustered at zoom-out; color-coded by grade (green A / yellow B / red C-or-below) at zoom-in.
- **Click/hover panel:** name, address, current grade/score, last inspection date, a small sparkline of score history, most recent violations, link back to the official source record.
- **Filters:** facility type, grade range, search by name/address, "recently inspected" toggle.
- **Performance:** read from a prebuilt GeoJSON (or vector tile set, if the point count justifies it) generated at ingestion time — don't hit a live database from the frontend. Same static-site principle as the AI news aggregator project.
- **Mobile-responsive.**

---

## 6. MCP server

A remote (HTTP/SSE) MCP server over the same DuckDB dataset — this needs to actually be reachable, not just run locally, since the point is that it "can be used."

Suggested tools:
- `search_establishments(query?, facility_type?, min_score?, max_score?, zip?)`
- `get_establishment(id)` — full record + inspection history
- `nearby_establishments(lat, lon, radius_miles, min_score?)`
- `score_distribution(facility_type?)` — aggregate stats, useful for "how does this score compare"

Given the underlying data is already public government records, read-only access without auth is reasonable — but if this server is publicly reachable, add basic rate-limiting so it can't be hammered or run up hosting costs.

---

## 7. Hosting/deployment

Two different hosting needs, since they have different runtime requirements:

- **The map site is static** — same pattern as the AI news aggregator: a scheduled GitHub Action re-scrapes, rebuilds the GeoJSON/site, and pushes; GitHub Pages serves it. No always-on server needed.
- **The MCP server is not static** — it needs a persistent running process to answer queries on demand. Given your existing Posit Connect exploration, that's a natural fit for hosting a small Python API/MCP server without standing up new infrastructure. A lightweight cloud host (Fly.io, Railway) is the alternative if you'd rather keep it outside your Posit environment.

---

## 8. Non-functional requirements

- No auth needed for the website — public, unpromoted URL.
- **Disclaimer + provenance matters here more than in the AI aggregator project:** this is health-and-safety-adjacent public data. Show a "last updated" date per facility, and link back to the official county source, with a clear note that this is an unofficial visualization, not the authoritative record. A restaurant's grade can change between your weekly scrapes — don't let the site imply it's live/current when it's a snapshot.
- Should degrade gracefully — a stale facility-type or a broken filter shouldn't take down the whole map.

---

## 9. Explicitly out of scope for v1 (stretch goals)

- Full historical trend view / time slider across all inspection history
- "Most improved / most declined" leaderboard
- Quality of Life Explorer neighborhood overlay as a toggleable choropleth
- Search-as-you-type autocomplete
- Usage analytics / rate-limit dashboard for the MCP server

---

## 10. Open decisions before handing this to Fable

1. **CDPEHS vs. EHIDS** — confirm which NC system is the actual current source of record for Mecklenburg data (§2a) before any scraper code gets written.
2. **Facility types in v1** — restaurants only, or pull the full Food & Facilities Sanitation scope (hotels, mobile food, child care, etc.) from day one now that it's nearly free to do so.
3. **Phase 2 civic layers** — ship Charlotte Code Enforcement + Quality of Life Explorer in v1, or treat strictly as later additions.
4. **MCP hosting target** — Posit Connect vs. a lightweight cloud host.
5. **MCP access** — fully open read access, or a simple shared API key since you mentioned "a few friends" specifically.

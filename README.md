# Charlotte Metro Food Grades

Interactive map of food-service inspection grades across Charlotte / Mecklenburg County,
NC, backed by scraped public inspection data, plus a live MCP server over the same dataset.

- **Map:** https://tkthomas27.github.io/charlotte-food-grades/
- **Data source:** Mecklenburg County Environmental Health via the state's public CDPEHS
  lookup (see [docs/DATA-SOURCE.md](docs/DATA-SOURCE.md) for the CDPEHS-vs-EHIDS decision
  and the reverse-engineered data paths)
- **Spec:** [docs/SPEC.md](docs/SPEC.md)

## How it works

```
CDPEHS (public.cdpehs.com, county 60)
   │  CSV export (date-chunked)  +  grid walk → violation pages
   ▼
pipeline/  ──►  data/db.duckdb  ──►  site/data/facilities.geojson ──► GitHub Pages (MapLibre)
   ▲                    │
   │                    └──► GitHub release asset `data-latest` ──► MCP server (Fly.io)
   └── weekly GitHub Action
```

- **Weekly refresh** ([.github/workflows/refresh.yml](.github/workflows/refresh.yml)):
  downloads the previous DuckDB from the `data-latest` release, grid-walks the last ~35
  days of inspections (learning internal CDP ids and fetching violation details), geocodes
  new facilities via the Census Bureau batch geocoder, exports GeoJSON, republishes the
  DB, and deploys the site to Pages.
- **History** is kept from ~2014 (earliest data in CDPEHS for Mecklenburg).

## Pipeline

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pipeline.ingest backfill --start 2014-01-01   # one-time seed
.venv/bin/python -m pipeline.ingest update --days 35              # incremental + violations
.venv/bin/python -m pipeline.ingest geocode                       # Census batch geocoder
.venv/bin/python -m pipeline.ingest export                        # site/data/*.geojson
.venv/bin/python -m pipeline.ingest weekly                        # update+geocode+export
```

Each source window is fetched independently — one failed window doesn't kill a run.
Every record carries `fetched_at`; the site shows per-facility provenance.

## Map site

Static, no backend: MapLibre GL JS over OpenFreeMap vector tiles, grade-colored clustered
points (green A / yellow B / red C-or-below / gray ungraded), a detail panel with score
sparkline, latest violations, and a link to the official county record. Filters: facility
type, grade, name/address search, recently-inspected. Serve locally with any static server:

```bash
python3 -m http.server -d site 8000
```

## MCP server

Remote MCP server (streamable HTTP at `/mcp`) over the same DuckDB file — read-only, no
auth, IP rate-limited. Tools: `search_establishments`, `get_establishment`,
`nearby_establishments`, `score_distribution`.

```bash
cd mcp-server
pip install -r requirements.txt
DB_PATH=../data/db.duckdb python server.py       # local
fly launch --no-deploy && fly deploy             # Fly.io; set DB_URL secret first
```

The deployed server boots by downloading the DuckDB file from the `data-latest` GitHub
release and re-checks every 24 h.

## Disclaimer

This is an **unofficial visualization** of public Mecklenburg County inspection records,
refreshed weekly. A facility's grade can change between refreshes;
[the county lookup](https://public.cdpehs.com/NCENVPBL/ESTABLISHMENT/ShowESTABLISHMENTTablePage.aspx?ESTTST_CTY=60)
is the authoritative record. Inspection type and permit status are not exposed by the
public county system and are omitted.

## Resolved decisions (2026-07-05)

1. **CDPEHS vs. EHIDS** — CDPEHS; EHIDS's API requires state CAS login ([details](docs/DATA-SOURCE.md)).
2. **Facility types in v1** — full Food & Facilities Sanitation scope, filterable on the map.
3. **Phase 2 civic layers** — deferred (Charlotte Code Enforcement, Quality of Life Explorer).
4. **MCP hosting** — Fly.io.
5. **MCP access** — open read-only + rate limiting.
